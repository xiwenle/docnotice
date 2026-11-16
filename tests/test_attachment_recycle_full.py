"""附件与回收站全链路专项测试。

本文件只使用真实 API、SQLite ORM 和文件系统，专门补强以下交叉语义：

- 资料“主文件 + 附件”的预览/默认下载方案 A；
- 附件替换、单独删除、恢复、永久删除；
- 归档时主文件与附件共用一条回收站记录，恢复和永久删除必须一起处理；
- 回收站筛选、分页、清空和通知回收站；
- 审计留痕与已下线的通知确认功能反向断言。

测试不访问真实 ``data/``；所有文件均在 pytest 临时 DATA_DIR 中创建。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from app.models.backup import RecycleBin
from app.models.document import FileRecord
from app.services import file_service
from files.generators import (
    DOCX_FILENAME,
    PDF_FILENAME,
    PPTX_FILENAME,
    make_docx,
    make_pdf,
    make_pptx,
    sha256_of,
)
from helpers import create_document, upload_document_file


pytestmark = pytest.mark.usefixtures("_data_isolation_guard")


# ---------------------------------------------------------------------------
# 测试工具：全部通过真实 API 建数据，不 mock 产品行为
# ---------------------------------------------------------------------------


def _error_code(resp) -> str | None:
    try:
        return (resp.json().get("error") or {}).get("code")
    except (TypeError, ValueError):
        return None


def _attach(
    client,
    headers: dict[str, str],
    doc_id: int,
    *,
    filename: str,
    content: bytes,
):
    return client.post(
        f"/api/v1/documents/{doc_id}/attachment",
        headers=headers,
        files={"file": (filename, content, "application/octet-stream")},
    )


def _make_document(
    client,
    headers: dict[str, str],
    *,
    title: str,
    effective_from: str = "2020-01-01",
) -> int:
    body = create_document(
        client,
        headers,
        title=title,
        effective_from=effective_from,
    )
    return int(body["id"])


def _upload_main(
    client,
    headers: dict[str, str],
    doc_id: int,
    *,
    filename: str,
    content: bytes,
) -> dict[str, Any]:
    resp = upload_document_file(
        client,
        headers,
        doc_id,
        filename=filename,
        content=content,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _document_with_main_and_attachment(
    client,
    headers: dict[str, str],
    tmp_path: Path,
    *,
    title: str,
) -> tuple[int, dict[str, Any], dict[str, Any], bytes, bytes]:
    """创建真实 PDF 主文件 + DOCX 附件，返回 API 文件和字节内容。"""
    doc_id = _make_document(client, headers, title=title)
    pdf = make_pdf(tmp_path / PDF_FILENAME).read_bytes()
    docx = make_docx(tmp_path / DOCX_FILENAME).read_bytes()
    main_body = _upload_main(
        client,
        headers,
        doc_id,
        filename=PDF_FILENAME,
        content=pdf,
    )
    attach_resp = _attach(
        client,
        headers,
        doc_id,
        filename=DOCX_FILENAME,
        content=docx,
    )
    assert attach_resp.status_code == 200, attach_resp.text
    return (
        doc_id,
        main_body["current_file"],
        attach_resp.json()["attachment_file"],
        pdf,
        docx,
    )


def _db_entry(
    db,
    *,
    reason: str,
    document_id: int | None = None,
) -> RecycleBin:
    db.expire_all()
    query = db.query(RecycleBin).filter(RecycleBin.reason == reason)
    if document_id is not None:
        query = query.filter(RecycleBin.document_id == document_id)
    entry = query.order_by(RecycleBin.id.desc()).first()
    assert entry is not None, f"未找到 reason={reason} 的回收站记录"
    db.refresh(entry)
    return entry


def _file(db, file_id: int) -> FileRecord:
    db.expire_all()
    rec = db.get(FileRecord, file_id)
    assert rec is not None, f"文件记录不存在: {file_id}"
    db.refresh(rec)
    return rec


def _file_abs_path(data_dir: Path, rec: FileRecord) -> Path:
    path = file_service.abs_path(rec.storage_rel_path)
    assert path.resolve().is_relative_to(data_dir.resolve())
    return path


def _parse_detail(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _audit_items(client, headers: dict[str, str], action: str) -> list[dict[str, Any]]:
    resp = client.get(
        f"/api/v1/audit-logs?action={action}&page_size=100",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def _create_targeted_notice(
    client,
    headers: dict[str, str],
    *,
    title: str,
    user_id: int,
) -> dict[str, Any]:
    resp = client.post(
        "/api/v1/notices",
        headers=headers,
        json={
            "title": title,
            "content": f"{title}正文",
            "notice_type": "发布",
            "target": {"scope": "user", "user_ids": [user_id]},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. 方案 A：默认下载附件，预览永远主文件
# ---------------------------------------------------------------------------


def test_download_plan_a_prefers_attachment_and_preview_uses_main(
    client, admin_headers, tmp_path
):
    doc_id, main, attachment, pdf, docx = _document_with_main_and_attachment(
        client,
        admin_headers,
        tmp_path,
        title="附件方案A专项",
    )

    # /file 是唯一默认下载语义：有附件时返回附件。
    default_dl = client.get(f"/api/v1/documents/{doc_id}/file", headers=admin_headers)
    assert default_dl.status_code == 200
    assert default_dl.content == docx
    assert len(default_dl.content) == attachment["size_bytes"]
    assert sha256_of(tmp_path / DOCX_FILENAME) == attachment["sha256"]

    # 附件专用下载端点同样返回附件。
    attachment_dl = client.get(
        f"/api/v1/documents/{doc_id}/attachment", headers=admin_headers
    )
    assert attachment_dl.status_code == 200
    assert attachment_dl.content == docx

    # 中文原文件名必须通过 RFC 5987 filename* 传输。
    for response in (default_dl, attachment_dl):
        disposition = response.headers["content-disposition"].lower()
        assert disposition.startswith("attachment")
        assert "filename*=utf-8''" in disposition
        assert quote(DOCX_FILENAME, safe="").lower() in disposition

    # 预览永远只走主文件，绝不能被附件顶替。
    preview = client.get(
        f"/api/v1/documents/{doc_id}/preview/pdf", headers=admin_headers
    )
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("application/pdf")
    assert preview.content == pdf

    detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers)
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["current_file"]["id"] == main["id"]
    assert detail_body["attachment_file"]["id"] == attachment["id"]


# ---------------------------------------------------------------------------
# 2. 替换附件：旧附件进入 replaced 记录，不占用主文件槽
# ---------------------------------------------------------------------------


def test_replace_attachment_records_old_attachment_slot(
    client, admin_headers, tmp_path, db, data_dir
):
    doc_id = _make_document(client, admin_headers, title="替换附件专项")
    old_docx = make_docx(tmp_path / DOCX_FILENAME).read_bytes()
    first = _attach(
        client,
        admin_headers,
        doc_id,
        filename=DOCX_FILENAME,
        content=old_docx,
    )
    assert first.status_code == 200, first.text
    old_attachment = first.json()["attachment_file"]

    new_pptx = make_pptx(tmp_path / PPTX_FILENAME).read_bytes()
    second = _attach(
        client,
        admin_headers,
        doc_id,
        filename=PPTX_FILENAME,
        content=new_pptx,
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["recycled_file_id"] == old_attachment["id"]
    assert body["attachment_file"]["id"] != old_attachment["id"]

    entry = _db_entry(db, reason="replaced", document_id=doc_id)
    assert entry.file_record_id is None
    assert entry.attachment_file_record_id == old_attachment["id"]

    old_rec = _file(db, old_attachment["id"])
    assert old_rec.status == "recycled"
    assert old_rec.storage_rel_path == f"recycle_bin/{old_rec.internal_name}"
    assert (data_dir / old_rec.storage_rel_path).is_file()

    detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
    assert detail["attachment_file"]["id"] == body["attachment_file"]["id"]
    active_files = (
        db.query(FileRecord)
        .filter(
            FileRecord.owner_type == "document",
            FileRecord.owner_id == doc_id,
            FileRecord.status == "active",
        )
        .all()
    )
    assert len(active_files) == 1, "替换附件后只应有一个 active 附件"


# ---------------------------------------------------------------------------
# 3. 单独删除附件：资料仍在，默认下载回落主文件
# ---------------------------------------------------------------------------


def test_delete_attachment_preserves_document_and_falls_back_to_main(
    client, admin_headers, user_headers, tmp_path, db, data_dir
):
    doc_id, _main, attachment, pdf, docx = _document_with_main_and_attachment(
        client,
        admin_headers,
        tmp_path,
        title="删除附件专项",
    )

    resp = client.delete(
        f"/api/v1/documents/{doc_id}/attachment", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text

    detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
    assert detail["attachment_file"] is None
    assert detail["archived"] is False

    entry = _db_entry(db, reason="attachment_deleted", document_id=doc_id)
    assert entry.file_record_id is None
    assert entry.attachment_file_record_id == attachment["id"]
    deleted_rec = _file(db, attachment["id"])
    assert deleted_rec.status == "recycled"
    assert (data_dir / f"recycle_bin/{deleted_rec.internal_name}").is_file()

    # 有附件时默认下载附件；删除附件后必须回落到当前主文件。
    default_dl = client.get(f"/api/v1/documents/{doc_id}/file", headers=admin_headers)
    assert default_dl.status_code == 200
    assert default_dl.content == pdf
    assert client.get(
        f"/api/v1/documents/{doc_id}/attachment", headers=admin_headers
    ).status_code == 404

    # 普通用户可以下载可见资料的当前文件，但不能修改附件槽。
    assert client.get(f"/api/v1/documents/{doc_id}/file", headers=user_headers).status_code == 200
    denied_delete = client.delete(
        f"/api/v1/documents/{doc_id}/attachment", headers=user_headers
    )
    assert denied_delete.status_code == 403
    assert _error_code(denied_delete) == "FORBIDDEN"
    denied_upload = _attach(
        client,
        user_headers,
        doc_id,
        filename=DOCX_FILENAME,
        content=docx,
    )
    assert denied_upload.status_code == 403
    assert _error_code(denied_upload) == "FORBIDDEN"


# ---------------------------------------------------------------------------
# 4. 恢复 attachment_deleted：文件回库并重新挂回原资料
# ---------------------------------------------------------------------------


def test_restore_deleted_attachment_reattaches_to_document(
    client, admin_headers, tmp_path, db, data_dir
):
    doc_id, _main, attachment, _pdf, _docx = _document_with_main_and_attachment(
        client,
        admin_headers,
        tmp_path,
        title="恢复删除附件专项",
    )
    assert client.delete(
        f"/api/v1/documents/{doc_id}/attachment", headers=admin_headers
    ).status_code == 200
    entry = _db_entry(db, reason="attachment_deleted", document_id=doc_id)
    assert _file(db, attachment["id"]).status == "recycled"

    resp = client.post(
        f"/api/v1/recycle-bin/{entry.id}/restore", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["restored_file_id"] == attachment["id"]

    db.expire_all()
    restored_entry = db.get(RecycleBin, entry.id)
    assert restored_entry is not None
    assert restored_entry.restored_at is not None

    restored_file = _file(db, attachment["id"])
    assert restored_file.status == "active"
    assert restored_file.storage_rel_path.startswith("documents/")
    assert _file_abs_path(data_dir, restored_file).is_file()
    assert not (data_dir / f"recycle_bin/{restored_file.internal_name}").exists()

    detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
    assert detail["archived"] is False
    assert detail["attachment_file"] is not None, "删除的附件恢复后必须重新挂回资料"
    assert detail["attachment_file"]["id"] == attachment["id"]
    assert client.get(
        f"/api/v1/documents/{doc_id}/attachment", headers=admin_headers
    ).content == _docx
    assert client.get(
        f"/api/v1/documents/{doc_id}/file", headers=admin_headers
    ).content == _docx, "方案 A：有附件时默认下载附件"

    repeated = client.post(
        f"/api/v1/recycle-bin/{entry.id}/restore", headers=admin_headers
    )
    assert repeated.status_code == 409
    assert _error_code(repeated) == "CONFLICT"




def test_restore_deleted_attachment_rejects_existing_attachment(
    client, admin_headers, tmp_path, db
):
    """删除后又上传新附件：恢复旧附件不得静默覆盖当前附件。"""
    doc_id, _main, old, _pdf, _docx = _document_with_main_and_attachment(
        client, admin_headers, tmp_path, title="恢复附件冲突-已有新附件"
    )
    assert client.delete(
        f"/api/v1/documents/{doc_id}/attachment", headers=admin_headers
    ).status_code == 200
    entry = _db_entry(db, reason="attachment_deleted", document_id=doc_id)
    new_att = _attach(
        client,
        admin_headers,
        doc_id,
        filename=PPTX_FILENAME,
        content=make_pptx(tmp_path / PPTX_FILENAME).read_bytes(),
    )
    assert new_att.status_code == 200, new_att.text
    current_id = new_att.json()["attachment_file"]["id"]

    resp = client.post(
        f"/api/v1/recycle-bin/{entry.id}/restore", headers=admin_headers
    )
    assert resp.status_code == 409
    assert _error_code(resp) == "CONFLICT"

    detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
    assert detail["attachment_file"]["id"] == current_id
    db.expire_all()
    assert db.get(RecycleBin, entry.id).restored_at is None


def test_restore_deleted_attachment_rejects_archived_document(
    client, admin_headers, tmp_path, db
):
    """资料归档后，不应把单独删除的旧附件挂回归档资料。"""
    doc_id, _main, old, _pdf, _docx = _document_with_main_and_attachment(
        client, admin_headers, tmp_path, title="恢复附件冲突-资料已归档"
    )
    assert client.delete(
        f"/api/v1/documents/{doc_id}/attachment", headers=admin_headers
    ).status_code == 200
    entry = _db_entry(db, reason="attachment_deleted", document_id=doc_id)
    archived = client.request(
        "DELETE",
        f"/api/v1/documents/{doc_id}",
        headers=admin_headers,
        json={"reason": "专项归档冲突"},
    )
    assert archived.status_code == 200, archived.text

    resp = client.post(
        f"/api/v1/recycle-bin/{entry.id}/restore", headers=admin_headers
    )
    assert resp.status_code == 409
    assert _error_code(resp) == "CONFLICT"

    detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
    assert detail["archived"] is True
    assert detail["attachment_file"] is None
    db.expire_all()
    assert db.get(RecycleBin, entry.id).restored_at is None


# ---------------------------------------------------------------------------
# 5. 永久删除 attachment_deleted：confirm 门禁、物理文件和重复操作
# ---------------------------------------------------------------------------


def test_purge_deleted_attachment_removes_physical_file(
    client, admin_headers, tmp_path, db, data_dir
):
    doc_id, _main, attachment, _pdf, _docx = _document_with_main_and_attachment(
        client,
        admin_headers,
        tmp_path,
        title="永久删除附件专项",
    )
    assert client.delete(
        f"/api/v1/documents/{doc_id}/attachment", headers=admin_headers
    ).status_code == 200
    entry = _db_entry(db, reason="attachment_deleted", document_id=doc_id)
    rec = _file(db, attachment["id"])
    recycled_path = _file_abs_path(data_dir, rec)
    assert recycled_path.is_file()

    no_confirm = client.request(
        "DELETE", f"/api/v1/recycle-bin/{entry.id}", headers=admin_headers
    )
    assert no_confirm.status_code == 400
    assert _error_code(no_confirm) == "BAD_REQUEST"
    assert recycled_path.is_file(), "缺少 confirm 时不得删除文件"

    purged = client.request(
        "DELETE",
        f"/api/v1/recycle-bin/{entry.id}",
        headers=admin_headers,
        json={"confirm": True},
    )
    assert purged.status_code == 200, purged.text
    assert purged.json()["status"] == "purged"
    assert not recycled_path.exists()

    db.expire_all()
    entry_after = db.get(RecycleBin, entry.id)
    assert entry_after is not None
    assert entry_after.purged_at is not None
    assert _file(db, attachment["id"]).status == "recycled"

    repeat_purge = client.request(
        "DELETE",
        f"/api/v1/recycle-bin/{entry.id}",
        headers=admin_headers,
        json={"confirm": True},
    )
    assert repeat_purge.status_code == 409
    repeat_restore = client.post(
        f"/api/v1/recycle-bin/{entry.id}/restore", headers=admin_headers
    )
    assert repeat_restore.status_code == 409


# ---------------------------------------------------------------------------
# 6. replaced 记录恢复：回文件库，但不覆盖当前附件
# ---------------------------------------------------------------------------


def test_restore_replaced_attachment_does_not_replace_current_attachment(
    client, admin_headers, tmp_path, db, data_dir
):
    doc_id = _make_document(client, admin_headers, title="恢复替换附件专项")
    old = _attach(
        client,
        admin_headers,
        doc_id,
        filename=DOCX_FILENAME,
        content=make_docx(tmp_path / DOCX_FILENAME).read_bytes(),
    ).json()["attachment_file"]
    new_pptx = make_pptx(tmp_path / PPTX_FILENAME).read_bytes()
    new = _attach(
        client,
        admin_headers,
        doc_id,
        filename=PPTX_FILENAME,
        content=new_pptx,
    ).json()["attachment_file"]
    entry = _db_entry(db, reason="replaced", document_id=doc_id)

    resp = client.post(
        f"/api/v1/recycle-bin/{entry.id}/restore", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["restored_file_id"] == old["id"]

    old_rec = _file(db, old["id"])
    new_rec = _file(db, new["id"])
    assert old_rec.status == "active"
    assert old_rec.storage_rel_path.startswith("documents/")
    assert _file_abs_path(data_dir, old_rec).is_file()
    assert new_rec.status == "active"

    detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
    assert detail["attachment_file"]["id"] == new["id"], "恢复旧附件不得顶掉当前附件"
    assert client.get(
        f"/api/v1/documents/{doc_id}/attachment", headers=admin_headers
    ).content == new_pptx


# ---------------------------------------------------------------------------
# 7. 归档主文件 + 附件：一条记录、两个文件槽、普通用户不可见
# ---------------------------------------------------------------------------


def test_archive_main_and_attachment_uses_one_entry_and_moves_both(
    client, admin_headers, user_headers, tmp_path, db, data_dir
):
    doc_id, main, attachment, _pdf, _docx = _document_with_main_and_attachment(
        client,
        admin_headers,
        tmp_path,
        title="归档双文件专项",
    )
    main_path = _file_abs_path(data_dir, _file(db, main["id"]))
    att_path = _file_abs_path(data_dir, _file(db, attachment["id"]))
    assert main_path.is_file() and att_path.is_file()

    archived = client.request(
        "DELETE",
        f"/api/v1/documents/{doc_id}",
        headers=admin_headers,
        json={"reason": "专项归档"},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"

    entries = client.get("/api/v1/recycle-bin", headers=admin_headers).json()["items"]
    matching = [e for e in entries if e["document_id"] == doc_id]
    assert len(matching) == 1, "主文件与附件必须共用一条归档记录"
    entry = matching[0]
    assert entry["reason"] == "archived"
    assert entry["file_record_id"] == main["id"]
    assert entry["attachment_file_record_id"] == attachment["id"]

    main_rec = _file(db, main["id"])
    att_rec = _file(db, attachment["id"])
    assert main_rec.status == att_rec.status == "recycled"
    assert main_rec.storage_rel_path == f"recycle_bin/{main_rec.internal_name}"
    assert att_rec.storage_rel_path == f"recycle_bin/{att_rec.internal_name}"
    assert not main_path.exists() and not att_path.exists()
    assert (data_dir / main_rec.storage_rel_path).is_file()
    assert (data_dir / att_rec.storage_rel_path).is_file()

    admin_detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers)
    assert admin_detail.status_code == 200
    assert admin_detail.json()["archived"] is True

    user_detail = client.get(f"/api/v1/documents/{doc_id}", headers=user_headers)
    assert user_detail.status_code == 403
    assert _error_code(user_detail) == "FORBIDDEN"
    assert client.get(
        f"/api/v1/documents/{doc_id}/file", headers=user_headers
    ).status_code == 403


# ---------------------------------------------------------------------------
# 8. 归档恢复：资料路由和回收站路由都必须完整恢复双文件
# ---------------------------------------------------------------------------


def test_restore_archived_document_via_both_routes_restores_both_files(
    client, admin_headers, tmp_path, db, data_dir
):
    doc_a, main_a, att_a, pdf_a, docx_a = _document_with_main_and_attachment(
        client,
        admin_headers,
        tmp_path,
        title="资料路由恢复双文件",
    )
    doc_b, main_b, att_b, pdf_b, docx_b = _document_with_main_and_attachment(
        client,
        admin_headers,
        tmp_path,
        title="回收站路由恢复双文件",
    )
    assert client.request(
        "DELETE", f"/api/v1/documents/{doc_a}", headers=admin_headers,
        json={"reason": "资料路由归档"},
    ).status_code == 200
    assert client.request(
        "DELETE", f"/api/v1/documents/{doc_b}", headers=admin_headers,
        json={"reason": "回收站路由归档"},
    ).status_code == 200
    entry_a = _db_entry(db, reason="archived", document_id=doc_a)
    entry_b = _db_entry(db, reason="archived", document_id=doc_b)

    restored_a = client.post(
        f"/api/v1/documents/{doc_a}/restore", headers=admin_headers
    )
    assert restored_a.status_code == 200, restored_a.text
    restored_b = client.post(
        f"/api/v1/recycle-bin/{entry_b.id}/restore", headers=admin_headers
    )
    assert restored_b.status_code == 200, restored_b.text
    assert restored_b.json()["reason"] == "archived"

    for doc_id, main, attachment, pdf, docx in (
        (doc_a, main_a, att_a, pdf_a, docx_a),
        (doc_b, main_b, att_b, pdf_b, docx_b),
    ):
        detail = client.get(
            f"/api/v1/documents/{doc_id}", headers=admin_headers
        ).json()
        assert detail["archived"] is False
        assert detail["current_file"]["id"] == main["id"]
        assert detail["attachment_file"]["id"] == attachment["id"]

        main_rec = _file(db, main["id"])
        att_rec = _file(db, attachment["id"])
        assert main_rec.status == att_rec.status == "active"
        assert main_rec.storage_rel_path.startswith("documents/")
        assert att_rec.storage_rel_path.startswith("documents/")
        assert _file_abs_path(data_dir, main_rec).is_file()
        assert _file_abs_path(data_dir, att_rec).is_file()

        default_dl = client.get(
            f"/api/v1/documents/{doc_id}/file", headers=admin_headers
        )
        assert default_dl.content == docx
        preview = client.get(
            f"/api/v1/documents/{doc_id}/preview/pdf", headers=admin_headers
        )
        assert preview.content == pdf

    db.expire_all()
    entry_a_after = db.get(RecycleBin, entry_a.id)
    entry_b_after = db.get(RecycleBin, entry_b.id)
    assert entry_a_after is not None and entry_a_after.restored_at is not None
    assert entry_b_after is not None and entry_b_after.restored_at is not None


# ---------------------------------------------------------------------------
# 9. 归档记录永久删除：主文件与附件一起物理删除
# ---------------------------------------------------------------------------


def test_purge_archived_entry_removes_main_and_attachment(
    client, admin_headers, tmp_path, db, data_dir
):
    doc_id, main, attachment, _pdf, _docx = _document_with_main_and_attachment(
        client,
        admin_headers,
        tmp_path,
        title="永久删除归档双文件",
    )
    assert client.request(
        "DELETE",
        f"/api/v1/documents/{doc_id}",
        headers=admin_headers,
        json={"reason": "待永久删除"},
    ).status_code == 200
    entry = _db_entry(db, reason="archived", document_id=doc_id)
    main_rec = _file(db, main["id"])
    att_rec = _file(db, attachment["id"])
    main_path = _file_abs_path(data_dir, main_rec)
    att_path = _file_abs_path(data_dir, att_rec)
    assert main_path.is_file() and att_path.is_file()

    no_confirm = client.request(
        "DELETE", f"/api/v1/recycle-bin/{entry.id}", headers=admin_headers
    )
    assert no_confirm.status_code == 400
    assert main_path.is_file() and att_path.is_file()

    purged = client.request(
        "DELETE",
        f"/api/v1/recycle-bin/{entry.id}",
        headers=admin_headers,
        json={"confirm": True},
    )
    assert purged.status_code == 200, purged.text
    assert not main_path.exists()
    assert not att_path.exists()
    db.expire_all()
    entry_after = db.get(RecycleBin, entry.id)
    assert entry_after is not None and entry_after.purged_at is not None

    assert client.request(
        "DELETE",
        f"/api/v1/recycle-bin/{entry.id}",
        headers=admin_headers,
        json={"confirm": True},
    ).status_code == 409
    assert client.post(
        f"/api/v1/recycle-bin/{entry.id}/restore", headers=admin_headers
    ).status_code == 409


# ---------------------------------------------------------------------------
# 10. 回收站列表：reason 过滤、分页元信息和普通用户拒绝
# ---------------------------------------------------------------------------


def test_recycle_bin_filters_pagination_and_permissions(
    client, admin_headers, user_headers, tmp_path, db
):
    # attachment_deleted
    doc_deleted, _main, _att, _pdf, _docx = _document_with_main_and_attachment(
        client, admin_headers, tmp_path, title="筛选-删除附件"
    )
    assert client.delete(
        f"/api/v1/documents/{doc_deleted}/attachment", headers=admin_headers
    ).status_code == 200

    # replaced
    doc_replaced = _make_document(client, admin_headers, title="筛选-替换附件")
    assert _attach(
        client,
        admin_headers,
        doc_replaced,
        filename=DOCX_FILENAME,
        content=make_docx(tmp_path / DOCX_FILENAME).read_bytes(),
    ).status_code == 200
    assert _attach(
        client,
        admin_headers,
        doc_replaced,
        filename=PPTX_FILENAME,
        content=make_pptx(tmp_path / PPTX_FILENAME).read_bytes(),
    ).status_code == 200

    # archived
    doc_archived, _main, _att, _pdf, _docx = _document_with_main_and_attachment(
        client, admin_headers, tmp_path, title="筛选-归档"
    )
    assert client.request(
        "DELETE", f"/api/v1/documents/{doc_archived}", headers=admin_headers
    ).status_code == 200

    expected = {
        "attachment_deleted": doc_deleted,
        "replaced": doc_replaced,
        "archived": doc_archived,
    }
    for reason, doc_id in expected.items():
        resp = client.get(
            f"/api/v1/recycle-bin?reason={reason}", headers=admin_headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["page"] == 1
        assert body["page_size"] == 20
        assert body["pages"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["reason"] == reason
        assert body["items"][0]["document_id"] == doc_id

    page = client.get(
        "/api/v1/recycle-bin?page=1&page_size=1", headers=admin_headers
    )
    assert page.status_code == 200
    page_body = page.json()
    assert page_body["total"] == 3
    assert page_body["page"] == 1
    assert page_body["page_size"] == 1
    assert page_body["pages"] == 3
    assert len(page_body["items"]) == 1

    assert client.get("/api/v1/recycle-bin", headers=user_headers).status_code == 403
    assert client.get(
        "/api/v1/recycle-bin/notices", headers=user_headers
    ).status_code == 403
    assert client.post(
        f"/api/v1/recycle-bin/{page_body['items'][0]['id']}/restore",
        headers=user_headers,
    ).status_code == 403


# ---------------------------------------------------------------------------
# 11. 清空回收站：文件、通知、审计汇总和空站幂等
# ---------------------------------------------------------------------------


def test_purge_all_clears_files_and_cancelled_notices_with_audit(
    client, admin_headers, normal_user, tmp_path, db, data_dir
):
    doc_id, main, attachment, _pdf, _docx = _document_with_main_and_attachment(
        client,
        admin_headers,
        tmp_path,
        title="清空回收站双文件",
    )
    main_path = _file_abs_path(data_dir, _file(db, main["id"]))
    att_path = _file_abs_path(data_dir, _file(db, attachment["id"]))
    assert client.request(
        "DELETE",
        f"/api/v1/documents/{doc_id}",
        headers=admin_headers,
        json={"reason": "清空演练"},
    ).status_code == 200
    entry = _db_entry(db, reason="archived", document_id=doc_id)
    main_path = _file_abs_path(data_dir, _file(db, main["id"]))
    att_path = _file_abs_path(data_dir, _file(db, attachment["id"]))

    notice = _create_targeted_notice(
        client,
        admin_headers,
        title="清空回收站通知",
        user_id=normal_user.id,
    )
    assert client.post(
        f"/api/v1/notices/{notice['id']}/cancel",
        headers=admin_headers,
        json={"reason": "清空演练"},
    ).status_code == 200

    no_confirm = client.post(
        "/api/v1/recycle-bin/purge-all", headers=admin_headers
    )
    assert no_confirm.status_code == 400
    assert main_path.is_file() and att_path.is_file()
    assert client.get(
        f"/api/v1/notices/{notice['id']}", headers=admin_headers
    ).status_code == 404

    purge = client.post(
        "/api/v1/recycle-bin/purge-all",
        headers=admin_headers,
        json={"confirm": True},
    )
    assert purge.status_code == 200, purge.text
    assert purge.json() == {"ok": True, "files_purged": 1, "notices_purged": 1}

    assert not main_path.exists()
    assert not att_path.exists()
    assert client.get("/api/v1/recycle-bin", headers=admin_headers).json()["total"] == 0
    assert client.get(
        "/api/v1/recycle-bin/notices", headers=admin_headers
    ).json()["items"] == []
    assert client.get(
        f"/api/v1/notices/{notice['id']}", headers=admin_headers
    ).status_code == 404
    db.expire_all()
    entry_after = db.get(RecycleBin, entry.id)
    assert entry_after is not None and entry_after.purged_at is not None

    summarize = [
        _parse_detail(item.get("detail"))
        for item in _audit_items(client, admin_headers, "recycle_purge")
    ]
    assert any(
        detail.get("via") == "purge_all"
        and detail.get("files_purged") == 1
        and detail.get("notices_purged") == 1
        for detail in summarize
    )

    empty_again = client.post(
        "/api/v1/recycle-bin/purge-all",
        headers=admin_headers,
        json={"confirm": True},
    )
    assert empty_again.status_code == 200
    assert empty_again.json() == {"ok": True, "files_purged": 0, "notices_purged": 0}


# ---------------------------------------------------------------------------
# 12. 通知回收站：取消、恢复、统计、批量恢复和彻底删除
# ---------------------------------------------------------------------------


def test_notice_recycle_full_flow_preserves_read_stats_and_bulk_restore(
    client, admin_headers, user_headers, normal_user
):
    first = _create_targeted_notice(
        client,
        admin_headers,
        title="通知回收站单条",
        user_id=normal_user.id,
    )
    second = _create_targeted_notice(
        client,
        admin_headers,
        title="通知回收站批量",
        user_id=normal_user.id,
    )

    read = client.post(
        f"/api/v1/notices/{first['id']}/read",
        headers=user_headers,
        json={},
    )
    assert read.status_code == 200, read.text
    assert read.json()["my_read_at"] is not None

    cancel_first = client.post(
        f"/api/v1/notices/{first['id']}/cancel",
        headers=admin_headers,
        json={"reason": "单条回收站"},
    )
    assert cancel_first.status_code == 200, cancel_first.text
    user_titles = [
        row["title"]
        for row in client.get("/api/v1/notices", headers=user_headers).json()["items"]
    ]
    assert "通知回收站单条" not in user_titles
    assert client.get(
        f"/api/v1/notices/{first['id']}", headers=user_headers
    ).status_code == 404
    deleted = client.get(
        "/api/v1/recycle-bin/notices", headers=admin_headers
    ).json()["items"]
    assert any(row["id"] == first["id"] for row in deleted)

    restored_first = client.post(
        f"/api/v1/recycle-bin/notices/{first['id']}/restore",
        headers=admin_headers,
    )
    assert restored_first.status_code == 200, restored_first.text
    assert restored_first.json()["status"] == "published"
    stats = client.get(
        f"/api/v1/notices/{first['id']}/statistics", headers=admin_headers
    )
    assert stats.status_code == 200, stats.text
    assert stats.json()["target_user_count"] == 1
    assert stats.json()["viewed_count"] == 1

    # 两条通知都再次进入回收站，批量恢复必须一条成功、一条跳过。
    for notice in (first, second):
        assert client.post(
            f"/api/v1/notices/{notice['id']}/cancel",
            headers=admin_headers,
            json={"reason": "批量恢复演练"},
        ).status_code == 200
    bulk = client.post(
        "/api/v1/recycle-bin/notices/bulk-restore",
        headers=admin_headers,
        json={"notice_ids": [first["id"], second["id"], 999999]},
    )
    assert bulk.status_code == 200, bulk.text
    bulk_body = bulk.json()
    assert {row["id"] for row in bulk_body["succeeded"]} == {first["id"], second["id"]}
    assert [row["id"] for row in bulk_body["skipped"]] == [999999]
    visible_titles = {
        row["title"]
        for row in client.get("/api/v1/notices", headers=user_headers).json()["items"]
    }
    assert {"通知回收站单条", "通知回收站批量"} <= visible_titles

    # 彻底删除只允许在回收站中；confirm 是强制门禁。
    assert client.post(
        f"/api/v1/notices/{first['id']}/cancel",
        headers=admin_headers,
        json={"reason": "待彻底删除"},
    ).status_code == 200
    no_confirm = client.request(
        "DELETE",
        f"/api/v1/recycle-bin/notices/{first['id']}",
        headers=admin_headers,
    )
    assert no_confirm.status_code == 400
    purged = client.request(
        "DELETE",
        f"/api/v1/recycle-bin/notices/{first['id']}",
        headers=admin_headers,
        json={"confirm": True},
    )
    assert purged.status_code == 200, purged.text
    assert client.get(
        f"/api/v1/notices/{first['id']}", headers=admin_headers
    ).status_code == 404


# ---------------------------------------------------------------------------
# 13. 审计留痕：上传、删除、恢复、单条 purge、purge-all 均可追溯
# ---------------------------------------------------------------------------


def test_attachment_and_recycle_actions_are_audited_without_secrets(
    client, admin_headers, tmp_path, db
):
    doc_id, _main, _attachment, _pdf, _docx = _document_with_main_and_attachment(
        client,
        admin_headers,
        tmp_path,
        title="审计留痕专项",
    )

    # 删除 → 恢复 → 再次上传 → 再删除 → 永久删除
    assert client.delete(
        f"/api/v1/documents/{doc_id}/attachment", headers=admin_headers
    ).status_code == 200
    deleted_entry = _db_entry(db, reason="attachment_deleted", document_id=doc_id)
    assert client.post(
        f"/api/v1/recycle-bin/{deleted_entry.id}/restore", headers=admin_headers
    ).status_code == 200

    assert _attach(
        client,
        admin_headers,
        doc_id,
        filename=DOCX_FILENAME,
        content=make_docx(tmp_path / DOCX_FILENAME).read_bytes(),
    ).status_code == 200
    assert client.delete(
        f"/api/v1/documents/{doc_id}/attachment", headers=admin_headers
    ).status_code == 200
    purge_entry = _db_entry(db, reason="attachment_deleted", document_id=doc_id)
    assert client.request(
        "DELETE",
        f"/api/v1/recycle-bin/{purge_entry.id}",
        headers=admin_headers,
        json={"confirm": True},
    ).status_code == 200

    # 空站 purge-all 也必须留下汇总审计。
    empty_purge = client.post(
        "/api/v1/recycle-bin/purge-all",
        headers=admin_headers,
        json={"confirm": True},
    )
    assert empty_purge.status_code == 200

    actions = {
        "set_document_attachment": _audit_items(
            client, admin_headers, "set_document_attachment"
        ),
        "delete_document_attachment": _audit_items(
            client, admin_headers, "delete_document_attachment"
        ),
        "recycle_restore": _audit_items(
            client, admin_headers, "recycle_restore"
        ),
        "recycle_purge": _audit_items(
            client, admin_headers, "recycle_purge"
        ),
    }
    assert len(actions["set_document_attachment"]) >= 2
    assert len(actions["delete_document_attachment"]) >= 2
    assert any(
        row["object_type"] == "recycle_bin" and row["object_id"] == deleted_entry.id
        for row in actions["recycle_restore"]
    )
    assert any(
        row["object_type"] == "recycle_bin" and row["object_id"] == purge_entry.id
        for row in actions["recycle_purge"]
    )
    assert any(
        _parse_detail(row.get("detail")).get("via") == "purge_all"
        for row in actions["recycle_purge"]
    )

    # 审计详情不得泄露密码、令牌或 Cookie 值。
    forbidden = ("password", "access_token", "authorization", "set-cookie", "secret_key")
    all_rows = [row for rows in actions.values() for row in rows]
    for row in all_rows:
        serialized = json.dumps(
            {
                "detail": row.get("detail"),
                "object_label": row.get("object_label"),
                "user_agent": row.get("user_agent"),
            },
            ensure_ascii=False,
        ).lower()
        assert not any(marker in serialized for marker in forbidden), serialized


# ---------------------------------------------------------------------------
# 14. 锁定语义反向断言：通知无附件字段/确认功能，页面无确认残留
# ---------------------------------------------------------------------------


def _all_json_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_all_json_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_all_json_keys(child))
    return keys


def test_locked_notice_semantics_have_no_attachment_or_confirmation_surface(
    client, admin_headers, user_headers, normal_user
):
    notice = _create_targeted_notice(
        client,
        admin_headers,
        title="锁定语义通知",
        user_id=normal_user.id,
    )

    detail = client.get(f"/api/v1/notices/{notice['id']}", headers=admin_headers)
    assert detail.status_code == 200
    payload = detail.json()
    keys = _all_json_keys(payload)
    assert not any(key.lower().startswith("attachment") for key in keys)
    raw_payload = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in (
        "confirmed_at",
        "confirmed_count",
        "unconfirmed_count",
        "confirmed_rate",
        "requires_confirmation",
    ):
        assert forbidden not in raw_payload
    assert payload["my_read_status"] in {"read", "unread"}

    stats = client.get(
        f"/api/v1/notices/{notice['id']}/statistics", headers=admin_headers
    )
    assert stats.status_code == 200
    stats_keys = _all_json_keys(stats.json())
    assert not any("confirm" in key.lower() for key in stats_keys)
    assert {
        "target_user_count",
        "viewed_count",
        "unviewed_count",
        "viewed_rate",
    } <= stats_keys

    # 已下线的确认/旧发布接口不能重新出现。
    confirm_route = client.post(
        f"/api/v1/notices/{notice['id']}/confirm",
        headers=user_headers,
        json={},
    )
    publish_route = client.post(
        f"/api/v1/notices/{notice['id']}/publish",
        headers=admin_headers,
        json={},
    )
    assert confirm_route.status_code in (404, 405)
    assert publish_route.status_code in (404, 405)

    # 页面壳也不能重新渲染确认阅读相关入口/字段。
    page = client.get("/notices", headers=user_headers)
    assert page.status_code == 200
    page_text = page.text.lower()
    for forbidden in ("确认阅读", "待确认", "确认率", "confirmed_at"):
        assert forbidden.lower() not in page_text
