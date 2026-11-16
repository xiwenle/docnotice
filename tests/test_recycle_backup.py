"""回收站 API + 备份恢复模块实测（t28 并行支线A）。

覆盖（项目规范 §25/§26/§28/§30–33、docs/API.md §8/§10、DESIGN §10）：

回收站
- 列表权限（admin+）、reason 过滤、分页
- replaced 恢复：文件回 documents/ 且**不回设为当前文件**（API.md §8 设计决策）
- archived/deleted 恢复：整条资料恢复（清 archived_at + 当前文件回位）
- purge：无 body / confirm=false → 400；任意 admin + confirm → 物理删除 + 软标记（权限模型 v3）
- 冲突：重复恢复/已恢复记录 purge/重复 purge → 409；审计 recycle_restore / recycle_purge

备份/恢复
- full 备份：目录结构（app.db + manifest.json + files/）、清单指纹一致、审计 backup_create
- database 备份：仅库、file_count=0；非法 type → 400
- 列表/详情（manifest 摘要）
- 恢复闭环：创建备份 → 改数据（新增/归档）→ 恢复 → 数据回到备份时点，
  安全备份可见、源记录 status='restored'、恢复后登录与写入仍可用
- 校验失败：缺文件 / 篡改 db 指纹 / 篡改 manifest 路径穿越 → 500 且不恢复（保持现状）
- 删除备份：无 body → 400；任意 admin + confirm → 物理删目录 + purged；重复删 → 409
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from files.generators import PDF_FILENAME, make_docx, make_pdf, sha256_of
from helpers import create_document, login_headers, upload_document_file

pytestmark = pytest.mark.usefixtures("_data_isolation_guard")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _make_doc_with_file(
    client, headers, tmp_path: Path, *, title: str, filename: str, fmt: str = "pdf"
) -> tuple[int, str]:
    """建资料 + 传真实文件，返回 (doc_id, 内部文件名 internal_name)。"""
    doc = create_document(client, headers, title=title)
    doc_id = doc["id"]
    if fmt == "pdf":
        f = make_pdf(tmp_path / filename)
    else:
        f = make_docx(tmp_path / filename)
    resp = upload_document_file(
        client, headers, doc_id, filename=filename, content=f.read_bytes()
    )
    assert resp.status_code == 200, f"上传失败 {resp.status_code}: {resp.text}"
    internal = resp.json()["current_file"]["id"]
    # 从文件记录反查内部名（storage 名由服务端生成）
    from app.db.session import SessionLocal
    from app.models.document import FileRecord

    session = SessionLocal()
    try:
        rec = session.get(FileRecord, internal)
        name = rec.internal_name if rec else None
    finally:
        session.close()
    assert name, "未找到文件记录"
    return doc_id, name


def _recycle_items(client, headers) -> list[dict]:
    resp = client.get("/api/v1/recycle-bin", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def _backup_dir(data_dir: Path, backup_path: str) -> Path:
    bdir = data_dir / backup_path
    assert bdir.is_dir(), f"备份目录缺失: {bdir}"
    return bdir


def _delete(client, url: str, headers: dict, json_body: dict | None = None):
    """DELETE 带 JSON body（本项目 TestClient 的 delete() 不支持 json 参数）。"""
    return client.request("DELETE", url, json=json_body, headers=headers)


# ---------------------------------------------------------------------------
# 1. 回收站
# ---------------------------------------------------------------------------
def test_recycle_bin_list_admin_only(client, admin_headers, user_headers):
    """列表权限：普通用户 403；admin 200 空列表。"""
    resp = client.get("/api/v1/recycle-bin", headers=user_headers)
    assert resp.status_code == 403
    resp = client.get("/api/v1/recycle-bin", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == [] and body["total"] == 0
    # 非法 reason → 400
    resp = client.get("/api/v1/recycle-bin?reason=hacked", headers=admin_headers)
    assert resp.status_code == 400


def test_recycle_replace_restore_not_current(
    client, admin_headers, user_headers, tmp_path, data_dir
):
    """文件替换进回收站（reason=replaced）；恢复只回文件库，不顶替当前文件。"""
    pdf_a = make_pdf(tmp_path / "旧版手册.pdf")
    pdf_b = make_pdf(tmp_path / PDF_FILENAME)
    doc = create_document(client, admin_headers, title="员工手册")
    doc_id = doc["id"]

    r = upload_document_file(client, admin_headers, doc_id, filename="旧版手册.pdf", content=pdf_a.read_bytes())
    assert r.status_code == 200
    r = upload_document_file(client, admin_headers, doc_id, filename=PDF_FILENAME, content=pdf_b.read_bytes())
    assert r.status_code == 200, r.text
    resp = r.json()
    assert resp["recycled_file_id"] is not None

    items = _recycle_items(client, admin_headers)
    assert len(items) == 1
    item = items[0]
    assert item["reason"] == "replaced"
    assert item["original_filename"] == "旧版手册.pdf"
    assert item["document_id"] == doc_id
    assert item["deleted_username"] == "admin"

    # 普通用户无权恢复
    entry_id = item["id"]
    resp = client.post(f"/api/v1/recycle-bin/{entry_id}/restore", headers=user_headers)
    assert resp.status_code == 403

    # admin 恢复
    resp = client.post(f"/api/v1/recycle-bin/{entry_id}/restore", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "restored"

    # 文件物理回位 documents/（origin_rel_path），当前文件仍是新文件
    from app.db.session import SessionLocal
    from app.models.backup import RecycleBin
    from app.models.document import FileRecord

    session = SessionLocal()
    try:
        entry = session.get(RecycleBin, entry_id)
        assert entry is not None and entry.restored_at is not None
        rec = session.get(FileRecord, entry.file_record_id)
        assert rec.status == "active"
        assert (data_dir / rec.storage_rel_path).is_file(), "文件未回到 documents/"
        old_internal = rec.internal_name
    finally:
        session.close()

    detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
    assert detail["current_file"] is not None
    assert detail["current_file"]["original_filename"] == PDF_FILENAME
    # 旧文件仍在 documents/（分片目录，作为“历史”副本，非当前文件）
    assert (data_dir / "documents" / old_internal[:2] / old_internal).is_file()

    # 已恢复记录再次恢复 → 409；审计存在
    resp = client.post(f"/api/v1/recycle-bin/{entry_id}/restore", headers=admin_headers)
    assert resp.status_code == 409
    logs = client.get("/api/v1/audit-logs?action=recycle_restore", headers=admin_headers).json()
    assert logs["total"] == 1


def test_recycle_archive_restore_whole_document(
    client, admin_headers, tmp_path, data_dir
):
    """归档资料恢复：整条资料回位（清 archived_at + 当前文件 active）。"""
    doc_id, internal = _make_doc_with_file(
        client, admin_headers, tmp_path, title="制度文件", filename=PDF_FILENAME
    )
    resp = _delete(client, f"/api/v1/documents/{doc_id}", admin_headers, {"reason": "不再使用"})
    assert resp.status_code == 200
    assert (data_dir / "recycle_bin" / internal).is_file()

    items = _recycle_items(client, admin_headers)
    assert len(items) == 1 and items[0]["reason"] == "archived"
    entry_id = items[0]["id"]

    resp = client.post(f"/api/v1/recycle-bin/{entry_id}/restore", headers=admin_headers)
    assert resp.status_code == 200, resp.text

    detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
    assert detail["archived"] is False
    assert detail["current_file"] is not None
    assert (data_dir / "documents" / internal[:2] / internal).is_file()
    assert not (data_dir / "recycle_bin" / internal).exists()


def test_recycle_purge_requires_confirm_admin_ok(
    client, admin_headers, user_factory, tmp_path, data_dir
):
    """purge：缺失/未确认 body → 400；任意 admin 权限通过，confirm=true → 物理删除+软标记（v3）。"""
    user_factory("manager", role="admin", password="manager1234")
    admin_only_headers = login_headers(client, "manager", "manager1234")

    doc_id, internal = _make_doc_with_file(
        client, admin_headers, tmp_path, title="待清除", filename=PDF_FILENAME
    )
    _delete(client, f"/api/v1/documents/{doc_id}", admin_headers)
    entry_id = _recycle_items(client, admin_headers)[0]["id"]
    rb_file = data_dir / "recycle_bin" / internal
    assert rb_file.is_file()

    # 无 body / confirm=false / 另一 admin 无 confirm（权限通过、卡在确认）
    resp = client.request("DELETE", f"/api/v1/recycle-bin/{entry_id}", headers=admin_headers)
    assert resp.status_code == 400, resp.text
    resp = _delete(client, f"/api/v1/recycle-bin/{entry_id}", admin_headers, {"confirm": False})
    assert resp.status_code == 400
    resp = _delete(client, f"/api/v1/recycle-bin/{entry_id}", admin_only_headers)
    assert resp.status_code == 400

    # admin 二次确认 → 物理删除 + 列表不再显示该记录（软标记行仅留审计）+ 审计
    resp = _delete(client, f"/api/v1/recycle-bin/{entry_id}", admin_headers, {"confirm": True})
    assert resp.status_code == 200, resp.text
    assert not rb_file.exists(), "物理文件应已被删除"

    items = _recycle_items(client, admin_headers)
    assert all(i["id"] != entry_id for i in items), "已永久删除的记录不应再出现在回收站列表"
    logs = client.get("/api/v1/audit-logs?action=recycle_purge", headers=admin_headers).json()
    assert logs["total"] == 1
    assert logs["items"][0]["object_id"] == entry_id

    # 重复 purge → 409；已恢复记录 purge → 409
    resp = _delete(client, f"/api/v1/recycle-bin/{entry_id}", admin_headers, {"confirm": True})
    assert resp.status_code == 409


def test_recycle_restore_after_purge_conflict(client, admin_headers, tmp_path):
    """已永久删除的记录恢复 → 409。"""
    doc_id, _ = _make_doc_with_file(
        client, admin_headers, tmp_path, title="清除后恢复", filename=PDF_FILENAME
    )
    _delete(client, f"/api/v1/documents/{doc_id}", admin_headers)
    entry_id = _recycle_items(client, admin_headers)[0]["id"]
    _delete(client, f"/api/v1/recycle-bin/{entry_id}", admin_headers, {"confirm": True})
    resp = client.post(f"/api/v1/recycle-bin/{entry_id}/restore", headers=admin_headers)
    assert resp.status_code == 409
    # 归档资料的文件已被物理删除 → 恢复资料应报明确错误（文件缺失）
    resp = client.post(f"/api/v1/documents/{doc_id}/restore", headers=admin_headers)
    assert resp.status_code in (404, 500)


def test_recycle_reason_filter(client, admin_headers, tmp_path):
    """reason 过滤：replaced / archived 各自可见。"""
    # replaced 一条
    doc = create_document(client, admin_headers, title="过滤测试")
    f1 = make_pdf(tmp_path / "a.pdf")
    f2 = make_pdf(tmp_path / "b.pdf")
    upload_document_file(client, admin_headers, doc["id"], filename="a.pdf", content=f1.read_bytes())
    upload_document_file(client, admin_headers, doc["id"], filename="b.pdf", content=f2.read_bytes())
    # archived 一条
    doc2_id, _ = _make_doc_with_file(client, admin_headers, tmp_path, title="归档过滤", filename="c.pdf")
    _delete(client, f"/api/v1/documents/{doc2_id}", admin_headers)

    replaced = client.get("/api/v1/recycle-bin?reason=replaced", headers=admin_headers).json()
    archived = client.get("/api/v1/recycle-bin?reason=archived", headers=admin_headers).json()
    assert replaced["total"] == 1 and replaced["items"][0]["reason"] == "replaced"
    assert archived["total"] == 1 and archived["items"][0]["reason"] == "archived"
    assert client.get("/api/v1/recycle-bin?reason=deleted", headers=admin_headers).json()["total"] == 0


# ---------------------------------------------------------------------------
# 2. 备份创建 / 列表 / 详情
# ---------------------------------------------------------------------------
def test_backup_create_full(client, admin_headers, user_headers, tmp_path, data_dir):
    """full 备份：目录结构 + manifest 指纹一致 + 审计；普通用户 403。"""
    # 记录本测试上传文件的内部名，用于核对它一定进入备份清单
    # （物理目录跨测试累积，其他历史文件也会被备份，因此只断言“包含”而非“等于”）
    _, internal = _make_doc_with_file(client, admin_headers, tmp_path, title="备份资料", filename=PDF_FILENAME)

    resp = client.post("/api/v1/backups", headers=user_headers, json={"type": "full"})
    assert resp.status_code == 403

    resp = client.post("/api/v1/backups", headers=admin_headers, json={"type": "full"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["file_count"] >= 1
    assert len(body["db_sha256"]) == 64

    bdir = _backup_dir(data_dir, body["backup_path"])
    assert (bdir / "app.db").is_file()
    manifest_file = bdir / "manifest.json"
    assert manifest_file.is_file()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["db_sha256"] == body["db_sha256"] == sha256_of(bdir / "app.db")
    assert manifest["file_count"] == body["file_count"] >= 1
    assert f"documents/{internal[:2]}/{internal}" in manifest["files"], "本次上传的文件必须进入备份清单"
    rel = next(iter(manifest["files"]))
    assert rel.startswith("documents/")
    assert sha256_of(bdir / "files" / rel) == manifest["files"][rel]
    # 备份目录不包含自身
    assert not any(p.name.startswith("backup_") for p in bdir.rglob("*") if p.is_file())

    logs = client.get("/api/v1/audit-logs?action=backup_create", headers=admin_headers).json()
    assert logs["total"] == 1
    assert logs["items"][0]["object_type"] == "backup"


def test_backup_database_type_and_invalid(client, admin_headers, data_dir):
    """database 类型仅库；非法 type → 400；type 过滤。"""
    resp = client.post("/api/v1/backups", headers=admin_headers, json={"type": "database"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["file_count"] == 0
    bdir = _backup_dir(data_dir, body["backup_path"])
    assert (bdir / "app.db").is_file()
    assert not (bdir / "files").exists() or not any((bdir / "files").iterdir())

    resp = client.post("/api/v1/backups", headers=admin_headers, json={"type": "evil"})
    assert resp.status_code == 400

    listed = client.get("/api/v1/backups?type=database", headers=admin_headers).json()
    assert listed["total"] == 1 and listed["items"][0]["backup_type"] == "database"


def test_backup_list_detail(client, admin_headers):
    """列表字段与详情 manifest 摘要。"""
    client.post("/api/v1/backups", headers=admin_headers, json={"type": "full"})
    listed = client.get("/api/v1/backups", headers=admin_headers).json()
    assert listed["total"] == 1
    item = listed["items"][0]
    for key in ("id", "backup_type", "backup_path", "size_bytes", "file_count", "db_sha256", "status", "created_at"):
        assert key in item

    detail = client.get(f"/api/v1/backups/{item['id']}", headers=admin_headers).json()
    assert detail["manifest"]["schema_version"] == 1
    assert detail["manifest"]["db_sha256"] == item["db_sha256"]


# ---------------------------------------------------------------------------
# 3. 备份恢复闭环
# ---------------------------------------------------------------------------
def test_backup_restore_roundtrip(
    client, admin_headers, user_factory, tmp_path, data_dir, db
):
    """核心闭环：备份 → 改数据（新增资料+归档资料）→ 恢复 → 数据回到备份时点。

    同时验证：confirm 二次确认、admin 权限（v3）、恢复前安全备份落盘、
    恢复后数据库可登录可写入、审计 backup_restore。
    """
    user_factory("manager2", role="admin", password="manager1234")
    login_headers(client, "manager2", "manager1234")

    # —— 备份时点 ——
    doc1_id, file1 = _make_doc_with_file(client, admin_headers, tmp_path, title="保留资料", filename=PDF_FILENAME)
    doc2_id, file2 = _make_doc_with_file(client, admin_headers, tmp_path, title="将归档", filename="归档前.pdf")

    resp = client.post("/api/v1/backups", headers=admin_headers, json={"type": "full"})
    assert resp.status_code == 201, resp.text
    backup = resp.json()
    backup_id = backup["id"]

    # —— 备份后改数据：新增 doc3 + 归档 doc2 ——
    doc3_id, file3 = _make_doc_with_file(client, admin_headers, tmp_path, title="新增资料", filename="新增.pdf")
    _delete(client, f"/api/v1/documents/{doc2_id}", admin_headers, {"reason": "备份后归档"})

    # confirm 校验
    r = client.post(f"/api/v1/backups/{backup_id}/restore", headers=admin_headers)
    assert r.status_code == 400, r.text          # 无 body
    r = client.post(f"/api/v1/backups/{backup_id}/restore", headers=admin_headers, json={})
    assert r.status_code == 400                  # confirm=false
    # 另一 admin 无 confirm → 400（权限通过、不真恢复）
    r = client.post(
        f"/api/v1/backups/{backup_id}/restore",
        headers=login_headers(client, "manager2", "manager1234"),
    )
    assert r.status_code == 400, r.text

    # 真正恢复（admin + confirm）；先关闭测试会话连接，避免换库后残留连接
    db.rollback()
    db.close()
    r = client.post(
        f"/api/v1/backups/{backup_id}/restore",
        headers=admin_headers,
        json={"confirm": True, "reason": "误操作回滚"},
    )
    assert r.status_code == 200, r.text
    restored = r.json()
    assert restored["status"] == "restored"
    assert "safety_backup_id" in restored

    # —— 数据回到备份时点 ——
    r = client.get(f"/api/v1/documents/{doc3_id}", headers=admin_headers)
    assert r.status_code in (404, 403), "备份后新增的资料应已回滚"
    r = client.get(f"/api/v1/documents/{doc1_id}", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["title"] == "保留资料"
    r = client.get(f"/api/v1/documents/{doc2_id}", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["archived"] is False, "归档操作已回滚"

    # 文件系统：documents 树只含备份时点文件（递归含分片子目录）
    doc_names = {p.name for p in (data_dir / "documents").rglob("*") if p.is_file()}
    assert file1 in doc_names and file2 in doc_names
    assert file3 not in doc_names, "备份后新增的文件应已移除（旧状态在安全备份中留旧）"
    # 归档产生的回收站条目随之回滚
    assert _recycle_items(client, admin_headers) == []

    # —— 恢复后记录与库可用性 ——
    # 2026-09-03 产品决策：恢复完成后已用备份直接删除（记录行 + 目录），
    # 回滚依赖恢复前安全备份；因此源备份不再出现在列表中
    listed = client.get("/api/v1/backups", headers=admin_headers).json()
    by_path = {b["backup_path"]: b for b in listed["items"]}
    source = by_path.get(backup["backup_path"])
    assert source is None, "已用于恢复的备份应被直接删除"
    assert not (data_dir / backup["backup_path"]).exists(), "已用备份目录应已删除"
    safety = next(b for b in listed["items"] if b["id"] == restored["safety_backup_id"])
    assert safety["status"] == "completed"
    assert safety["note"].startswith("恢复前自动安全备份")
    assert (data_dir / safety["backup_path"]).is_dir(), "安全备份目录应存在"

    logs = client.get("/api/v1/audit-logs?action=backup_restore", headers=admin_headers).json()
    assert logs["total"] == 1

    # 恢复后登录 + 写入正常（数据库重新连接可用，§28）
    token = login_headers(client, "admin", "admin1234")
    resp = client.post("/api/v1/documents", headers=token, json={
        "title": "恢复后新建", "effective_from": "2026-01-01",
    })
    assert resp.status_code == 201, resp.text


def test_backup_restore_rejects_corrupt(client, admin_headers, tmp_path, data_dir, db):
    """校验失败（缺文件/篡改 db 指纹/路径穿越）→ 500 且保持现状。"""
    doc1_id, file1 = _make_doc_with_file(client, admin_headers, tmp_path, title="恢复前资料", filename=PDF_FILENAME)
    resp = client.post("/api/v1/backups", headers=admin_headers, json={"type": "full"})
    backup = resp.json()
    backup_id = backup["id"]
    bdir = _backup_dir(data_dir, backup["backup_path"])

    # 恢复前再改一点数据，用于断言“未受影响”
    doc2_id, file2 = _make_doc_with_file(client, admin_headers, tmp_path, title="改动资料", filename="改动.pdf")

    # (a) 删除备份内一个业务文件 → 校验失败
    target = next(p for p in (bdir / "files").rglob("*") if p.is_file())
    target.unlink()
    r = client.post(f"/api/v1/backups/{backup_id}/restore", headers=admin_headers, json={"confirm": True})
    assert r.status_code == 500, r.text
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert client.get(f"/api/v1/documents/{doc2_id}", headers=admin_headers).status_code == 200

    # (b) 篡改 db 指纹（manifest）→ 校验失败（记录指纹与文件指纹双重校验）
    manifest_file = bdir / "manifest.json"
    m = json.loads(manifest_file.read_text(encoding="utf-8"))
    m["db_sha256"] = "0" * 64
    manifest_file.write_text(json.dumps(m), encoding="utf-8")
    r = client.post(f"/api/v1/backups/{backup_id}/restore", headers=admin_headers, json={"confirm": True})
    assert r.status_code == 500
    assert client.get(f"/api/v1/documents/{doc2_id}", headers=admin_headers).status_code == 200

    # (c) 篡改记录指纹（回滚 db 指纹，改 record.db_sha256）→ 仍拒绝
    m["db_sha256"] = sha256_of(bdir / "app.db")
    manifest_file.write_text(json.dumps(m), encoding="utf-8")
    from app.db.session import SessionLocal
    from app.models.backup import BackupRecord

    session = SessionLocal()
    try:
        rec = session.get(BackupRecord, backup_id)
        rec.db_sha256 = "f" * 64
        session.commit()
    finally:
        session.close()
    r = client.post(f"/api/v1/backups/{backup_id}/restore", headers=admin_headers, json={"confirm": True})
    assert r.status_code == 500
    assert client.get(f"/api/v1/documents/{doc2_id}", headers=admin_headers).status_code == 200

    # (d) 路径穿越 manifest → 拒绝（不把文件写出 DATA_DIR；先复位记录指纹让
    #     校验走到文件清单阶段）
    session = SessionLocal()
    try:
        rec = session.get(BackupRecord, backup_id)
        rec.db_sha256 = m["db_sha256"]
        session.commit()
    finally:
        session.close()
    m["files"] = {"../../evil.txt": sha256_of(bdir / "app.db")}
    manifest_file.write_text(json.dumps(m), encoding="utf-8")
    r = client.post(f"/api/v1/backups/{backup_id}/restore", headers=admin_headers, json={"confirm": True})
    assert r.status_code == 500
    assert not (data_dir.parent / "evil.txt").exists()

    db.rollback()
    db.close()


def test_backup_delete_requires_confirm_admin_ok(client, admin_headers, user_factory, data_dir):
    """删除备份：无 body → 400；任意 admin + confirm → 真删除（目录+记录行）；
    重复删 → 404（记录已不存在，2026-08-30 真删除语义）。"""
    user_factory("manager3", role="admin", password="manager1234")
    mgr_headers = login_headers(client, "manager3", "manager1234")

    resp = client.post("/api/v1/backups", headers=admin_headers, json={"type": "full"})
    backup_id = resp.json()["id"]
    bdir = _backup_dir(data_dir, resp.json()["backup_path"])

    r = client.request("DELETE", f"/api/v1/backups/{backup_id}", headers=admin_headers)
    assert r.status_code == 400                     # 无 body
    r = _delete(client, f"/api/v1/backups/{backup_id}", mgr_headers)
    assert r.status_code == 400                     # 另一 admin 无 confirm（权限通过）
    r = _delete(client, f"/api/v1/backups/{backup_id}", admin_headers, {"confirm": True})
    assert r.status_code == 200, r.text
    assert not bdir.exists(), "备份目录应被物理删除"

    # 真删除：记录行删除，列表不再出现（无 purged 软标记行）
    items = client.get("/api/v1/backups", headers=admin_headers).json()["items"]
    assert all(i["id"] != backup_id for i in items)
    logs = client.get("/api/v1/audit-logs?action=backup_delete", headers=admin_headers).json()
    assert logs["total"] == 1

    r = _delete(client, f"/api/v1/backups/{backup_id}", admin_headers, {"confirm": True})
    assert r.status_code == 404                     # 记录已不存在
    r = _delete(client, "/api/v1/backups/99999", admin_headers, {"confirm": True})
    assert r.status_code == 404

def test_recycle_bin_purge_all(client, admin_headers, tmp_path):
    """清空回收站：文件条目与已删除通知一并物理清空（2026-08-30）。

    - 响应计数正确；两区皆空；被清通知行彻底消失
    - 汇总审计（recycle_purge, via=purge_all）可追溯
    """
    # 2026-11-03：管理员不进阅读对象，发布需至少一个普通读者
    client.post("/api/v1/users", headers=admin_headers, json={
        "username": "purge_reader", "password": "user1234",
        "display_name": "清空读者", "role": "user",
    })
    doc = create_document(client, admin_headers, title="清空演练资料")
    pdf = make_pdf(tmp_path / "清空演练.pdf")
    assert upload_document_file(
        client, admin_headers, doc["id"], filename="清空演练.pdf", content=pdf.read_bytes()
    ).status_code == 200
    assert client.delete(f"/api/v1/documents/{doc['id']}", headers=admin_headers).status_code == 200

    n = client.post(
        "/api/v1/notices", headers=admin_headers,
        json={"title": "清空演练通知", "notice_type": "发布", "content": "演练"},
    ).json()  # 2026-11-04：创建即发布，无需再调 /publish
    assert client.post(
        f"/api/v1/notices/{n['id']}/cancel", headers=admin_headers,
        json={"reason": "清空演练"},
    ).status_code == 200

    # 前置：文件条目 1、已删除通知 1
    assert client.get("/api/v1/recycle-bin", headers=admin_headers).json()["total"] == 1
    notices_before = client.get("/api/v1/recycle-bin/notices", headers=admin_headers).json()["items"]
    assert len(notices_before) == 1

    r = client.post("/api/v1/recycle-bin/purge-all", headers=admin_headers,
                     json={"confirm": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["files_purged"] == 1 and body["notices_purged"] == 1

    # 两区皆空、通知行彻底消失
    assert client.get("/api/v1/recycle-bin", headers=admin_headers).json()["total"] == 0
    assert client.get("/api/v1/recycle-bin/notices", headers=admin_headers).json()["items"] == []
    assert client.get(f"/api/v1/notices/{n['id']}", headers=admin_headers).status_code == 404

    # 汇总审计（via=purge_all）可追溯
    logs = client.get("/api/v1/audit-logs?action=recycle_purge", headers=admin_headers).json()
    assert any("purge_all" in (entry.get("detail") or "") for entry in logs["items"])
