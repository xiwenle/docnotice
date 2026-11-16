"""备份恢复边界回归（船长抽查 Bug）：

零业务文件系统上 备份→恢复 曾崩溃（500 FileNotFoundError：create_backup 不建
files/ 目录而 restore 无条件 copytree）。本文件验证修复后的三条路径：
1. 零文件 full 备份：files/ 目录存在、file_count=0、manifest 指纹有效
2. 零文件恢复闭环：恢复 200 + 安全备份 + DB 回到备份时点
3. 正常有文件备份/恢复回归（files/documents 与 files/recycle_bin 落位）
4. 旧版备份（目录内无 files/）恢复容错：仅恢复库，不崩溃
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from files.generators import make_pdf
from helpers import create_document, upload_document_file

pytestmark = pytest.mark.usefixtures("_data_isolation_guard")


@pytest.fixture(autouse=True)
def _clean_business_files(data_dir):
    """本文件以“空业务文件系统”为断言前提（file_count=0 / documents 目录恰 1 个文件）。

    测试会话的临时 DATA_DIR 是全 suite 共享的，其他测试文件会向 documents/ 等
    写入物理文件 —— 每个用例前清空业务子目录（含分片子目录），恢复零文件前提（不动数据库）。
    """
    import shutil

    for sub in ("documents", "notices", "uploads", "recycle_bin"):
        d = data_dir / sub
        if d.exists():
            for p in list(d.iterdir()):
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
    yield


def _create_full_backup(client, headers) -> dict:
    resp = client.post("/api/v1/backups", headers=headers, json={"type": "full"})
    assert resp.status_code == 201, f"备份失败 {resp.status_code}: {resp.text}"
    return resp.json()


def _restore_backup(client, headers, backup_id: int, *, confirm: bool = True) -> object:
    resp = client.post(
        f"/api/v1/backups/{backup_id}/restore",
        headers=headers,
        json={"confirm": confirm, "reason": "边界回归测试"},
    )
    return resp


def _backup_dir(data_dir: Path, backup_path: str) -> Path:
    bdir = data_dir / backup_path
    assert bdir.is_dir(), f"备份目录缺失: {bdir}"
    return bdir


# ---------------------------------------------------------------------------
# 1. 零业务文件：备份 + 恢复闭环
# ---------------------------------------------------------------------------
def test_zero_file_backup_creates_files_dir(client, admin_headers, data_dir):
    """零文件全量备份：files/ 必须存在（修复点①），file_count=0，manifest 指纹有效。"""
    backup = _create_full_backup(client, admin_headers)
    assert backup["file_count"] == 0, f"零文件备份 file_count 应为 0: {backup}"
    assert backup["status"] == "completed"

    bdir = _backup_dir(data_dir, backup["backup_path"])
    files_dir = bdir / "files"
    assert files_dir.is_dir(), "零文件备份也必须创建 files/ 目录（修复点①）"
    assert (bdir / "app.db").exists(), "备份必须包含数据库快照"

    manifest = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["file_count"] == 0
    assert manifest["files"] == {}
    assert manifest["db_sha256"], "manifest 必须含 db_sha256"


def test_zero_file_restore_roundtrip(client, admin_headers, data_dir):
    """零文件恢复闭环：200 + 安全备份 + DB 回滚到备份时点（修复点②）。"""
    # 备份前：无资料
    resp = client.get("/api/v1/documents", headers=admin_headers)
    assert resp.json()["total"] == 0

    backup = _create_full_backup(client, admin_headers)
    backup_id = backup["id"]

    # 备份后制造变化：新增资料（业务变化只存在于 DB，无文件）
    resp = client.post(
        "/api/v1/documents",
        headers=admin_headers,
        json={"title": "恢复后不应存在", "effective_from": "2026-01-01"},
    )
    assert resp.status_code == 201, resp.text
    resp = client.get("/api/v1/documents", headers=admin_headers)
    assert resp.json()["total"] == 1

    # 恢复
    resp = _restore_backup(client, admin_headers, backup_id)
    assert resp.status_code == 200, f"零文件恢复失败 {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["status"] == "restored"
    assert body["safety_backup_id"], "恢复前必须自动生成安全备份"

    # DB 回到备份时点
    resp = client.get("/api/v1/documents", headers=admin_headers)
    assert resp.json()["total"] == 0, "恢复后新增资料应消失"

    # 恢复后应用仍可写（连接已刷新）
    resp = client.post(
        "/api/v1/documents",
        headers=admin_headers,
        json={"title": "恢复后可再写入", "effective_from": "2026-01-01"},
    )
    assert resp.status_code == 201, resp.text

    # 业务子目录仍存在（不含备份目录自身）
    for sub in ("documents", "notices", "recycle_bin", "uploads"):
        assert (data_dir / sub).is_dir(), f"恢复后业务目录缺失: {sub}"


def test_zero_file_restore_requires_confirm(client, admin_headers):
    """零文件场景下 confirm 契约不变（API.md §10）。"""
    backup = _create_full_backup(client, admin_headers)
    resp = _restore_backup(client, admin_headers, backup["id"], confirm=False)
    assert resp.status_code == 400, resp.text


def test_restore_survives_windows_locked_db_files(
    client, admin_headers, db_path, monkeypatch
):
    """Windows 语义回归（P0，2026-10-21）。

    Windows 上只要还有 SQLite 连接持有句柄，unlink -wal/-shm 抛 WinError 32，
    os.replace 主库抛 WinError 5。恢复必须走 SQLite online backup，不得依赖
    删除/替换文件；否则恢复在 Windows 上必然 500。
    """
    import os
    import sqlite3

    backup = _create_full_backup(client, admin_headers)
    resp = client.post(
        "/api/v1/documents",
        headers=admin_headers,
        json={"title": "恢复后不应存在", "effective_from": "2026-01-01"},
    )
    assert resp.status_code == 201, resp.text

    # 持有连接：制造 -wal/-shm，并模拟 Windows 的句柄占用
    holder = sqlite3.connect(str(db_path))
    try:
        real_replace = os.replace

        def deny_replace(src, dst, *args, **kwargs):
            if Path(dst) == db_path:
                raise PermissionError(5, "拒绝访问（模拟 Windows 占用）", str(dst))
            return real_replace(src, dst, *args, **kwargs)

        real_unlink = Path.unlink

        def deny_sidecar_unlink(self, *args, **kwargs):
            if self.parent == db_path.parent and self.name in (
                f"{db_path.name}-wal",
                f"{db_path.name}-shm",
            ):
                raise PermissionError(32, "文件被占用（模拟 Windows）", str(self))
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(os, "replace", deny_replace)
        monkeypatch.setattr(Path, "unlink", deny_sidecar_unlink)

        resp = _restore_backup(client, admin_headers, backup["id"])
    finally:
        holder.close()

    assert resp.status_code == 200, f"Windows 语义下恢复失败 {resp.status_code}: {resp.text}"
    assert resp.json()["status"] == "restored"
    assert client.get("/api/v1/documents", headers=admin_headers).json()["total"] == 0


# ---------------------------------------------------------------------------
# 2. 正常有文件备份/恢复回归
# ---------------------------------------------------------------------------
def test_backup_restore_roundtrip_with_files(client, admin_headers, data_dir, tmp_path):
    """有文件回归：files/documents 与 files/recycle_bin 落位，恢复后文件回位。"""
    # 建资料 + 传 PDF → 替换（产生 recycle_bin 文件）
    doc = create_document(client, admin_headers, title="有文件资料A")
    doc_id = doc["id"]
    f1 = make_pdf(tmp_path / "版本1.pdf")
    resp = upload_document_file(
        client, admin_headers, doc_id, filename="版本1.pdf", content=f1.read_bytes()
    )
    assert resp.status_code == 200, resp.text
    f2 = make_pdf(tmp_path / "版本2.pdf")
    resp = upload_document_file(
        client, admin_headers, doc_id, filename="版本2.pdf", content=f2.read_bytes()
    )
    assert resp.status_code == 200, resp.text

    backup = _create_full_backup(client, admin_headers)
    assert backup["file_count"] >= 2, f"有文件备份 file_count: {backup}"

    bdir = _backup_dir(data_dir, backup["backup_path"])
    files_root = bdir / "files"
    assert files_root.is_dir()
    assert (files_root / "documents").is_dir(), "files/documents 必须落位"
    assert (files_root / "recycle_bin").is_dir(), "files/recycle_bin 必须落位"
    doc_files = [
        p for p in (files_root / "documents").rglob("*") if p.is_file()
    ]
    assert len(doc_files) == 1, f"documents 应有 1 个当前文件: {doc_files}"

    # 备份后归档资料，再恢复 → 资料应回到备份时点（有效状态）
    resp = client.request(
        "DELETE", f"/api/v1/documents/{doc_id}",
        headers=admin_headers, json={"reason": "测试归档"},
    )
    assert resp.status_code == 200, resp.text

    resp = _restore_backup(client, admin_headers, backup["id"])
    assert resp.status_code == 200, f"有文件恢复失败 {resp.status_code}: {resp.text}"

    resp = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers)
    assert resp.status_code == 200, "恢复后资料应存在且有效"
    detail = resp.json()
    assert detail["archived"] is False
    assert detail["current_file"] is not None, "恢复后当前文件应回位"


# ---------------------------------------------------------------------------
# 3. 旧版备份（无 files/ 目录）容错
# ---------------------------------------------------------------------------
def test_restore_backup_without_files_dir_tolerated(client, admin_headers, data_dir):
    """兼容旧版/极简备份：目录内只有 app.db+manifest.json 无 files/ → 仅恢复库不崩溃。"""
    backup = _create_full_backup(client, admin_headers)
    bdir = _backup_dir(data_dir, backup["backup_path"])

    # 模拟旧版备份：删除 files/ 目录（manifest 同步改为空清单，保持自洽）
    import shutil

    shutil.rmtree(bdir / "files")
    manifest = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] = {}
    manifest["file_count"] = 0
    (bdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    # 同步修正 backup_records 指纹（file_count）
    from app.db.session import SessionLocal
    from app.models.backup import BackupRecord

    session = SessionLocal()
    try:
        rec = session.get(BackupRecord, backup["id"])
        assert rec is not None
        rec.file_count = 0
        session.commit()
    finally:
        session.close()

    resp = _restore_backup(client, admin_headers, backup["id"])
    assert resp.status_code == 200, f"缺失 files/ 恢复应容错 {resp.status_code}: {resp.text}"
    assert resp.json()["status"] == "restored"