"""回收站服务（docs/API.md §8、DATABASE.md §15、项目规范 §26）。

- list：替换文件/归档资料/删除资料记录
- restore：replaced → 文件回 documents/（不作为当前文件）；archived/deleted → 恢复整条资料
- purge：物理删除（仅 admin + confirm=true 二次确认），软标记不删行
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.session import total_count
from app.models.backup import RecycleBin
from app.models.document import Document, FileRecord
from app.models.notice import Notice
from app.models.user import User
from app.schemas.common import page_meta
from app.services import audit_service, file_service

logger = logging.getLogger(__name__)

REASONS = ("replaced", "archived", "deleted", "attachment_deleted")


def list_recycle(
    db: Session,
    *,
    reason: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """回收站列表：已永久删除（purged）或已恢复（restored）的记录不再显示。"""
    stmt = (
        select(RecycleBin)
        .where(
            RecycleBin.purged_at.is_(None),
            RecycleBin.restored_at.is_(None),  # 已恢复的也不显示
        )
        .order_by(RecycleBin.id.desc())
    )
    if reason:
        if reason not in REASONS:
            raise AppError("VALIDATION_ERROR", f"reason 仅支持 {'/'.join(REASONS)}")
        stmt = stmt.where(RecycleBin.reason == reason)

    total = total_count(db, stmt)
    entries = db.execute(
        stmt.offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()

    items = []
    for e in entries:
        item = _entry_to_dict(db, e)
        items.append(item)
    return {"items": items, **page_meta(total, page, page_size)}


def _entry_to_dict(db: Session, e: RecycleBin) -> dict:
    rec = db.get(FileRecord, e.file_record_id) if e.file_record_id else None
    att = (
        db.get(FileRecord, e.attachment_file_record_id)
        if e.attachment_file_record_id
        else None
    )
    doc = db.get(Document, e.document_id) if e.document_id else None
    actor = db.get(User, e.deleted_by) if e.deleted_by else None
    # 无文件资料被删除时无文件记录：以资料标题代替文件名展示。
    # 2026-11-06：只有附件的条目（替换/删除附件）优先显示附件名，
    # 否则「原文件名」列会显示资料标题，与真正的文件名对不上。
    display_name = (
        rec.original_filename if rec
        else (att.original_filename if att
              else (doc.title if doc else None))
    )
    return {
        "id": e.id,
        "reason": e.reason,
        "document_id": e.document_id,
        "document_title": doc.title if doc else None,
        "file_record_id": e.file_record_id,
        "original_filename": display_name,
        "file_type": rec.file_type if rec else None,
        "size_bytes": rec.size_bytes if rec else None,
        # 附件（2026-11-06）：归档条目里可能同时带着附件
        "attachment_file_record_id": e.attachment_file_record_id,
        "attachment_filename": att.original_filename if att else None,
        "attachment_file_type": att.file_type if att else None,
        "attachment_size_bytes": att.size_bytes if att else None,
        "deleted_at": e.deleted_at,
        "deleted_by": e.deleted_by,
        "deleted_username": actor.username if actor else None,
        "restored_at": e.restored_at,
        "purged_at": e.purged_at,
    }


def _load_entry(db: Session, entry_id: int) -> RecycleBin:
    e = db.get(RecycleBin, entry_id)
    if e is None:
        raise AppError("NOT_FOUND", "回收站记录不存在")
    if e.restored_at is not None or e.purged_at is not None:
        raise AppError("CONFLICT", "该记录已恢复/已彻底删除")
    return e


def restore(
    db: Session,
    *,
    current_user: User,
    entry_id: int,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """恢复回收站记录（§26、API.md §8）。"""
    e = _load_entry(db, entry_id)
    rec = db.get(FileRecord, e.file_record_id) if e.file_record_id else None
    doc = db.get(Document, e.document_id) if e.document_id else None

    att = (
        db.get(FileRecord, e.attachment_file_record_id)
        if e.attachment_file_record_id
        else None
    )
    restored_file_id: Optional[int] = None
    if e.reason == "replaced":
        # 文件回 documents/ 并置 active；不作为当前文件（避免与现版本冲突）。
        # 附件替换产生的 replaced 记录把旧附件放在 attachment_file_record_id，
        # 不能只查 file_record_id，否则旧附件永远无法恢复。
        target = rec if rec is not None else att
        if target is None:
            raise AppError("NOT_FOUND", "文件记录不存在")
        origin = e.origin_rel_path or file_service.storage_rel_path(
            "document", target.internal_name
        )
        file_service.restore_recycled_file(
            db, file_record=target, origin_rel_path=origin, actor=current_user
        )
        restored_file_id = target.id
    elif e.reason == "attachment_deleted":
        # 删除的附件恢复：文件回库并重新挂回原资料，用户可在详情中看到/下载。
        # 为防静默覆盖，资料必须未归档且当前没有 active 附件；否则明确 409。
        if att is None:
            raise AppError("NOT_FOUND", "附件记录不存在")
        if doc is None:
            raise AppError("NOT_FOUND", "关联资料不存在")
        if doc.archived_at is not None:
            raise AppError("CONFLICT", "资料已归档，请先恢复资料后再恢复附件")
        current_att = doc.attachment_file
        if current_att is not None and current_att.status == "active":
            raise AppError("CONFLICT", "资料已有当前附件，不能覆盖；请先删除当前附件")
        file_service.restore_recycled_file(
            db,
            file_record=att,
            origin_rel_path=file_service.storage_rel_path(
                "document", att.internal_name
            ),
            actor=current_user,
        )
        doc.attachment_file_id = att.id
        doc.updated_by = current_user.id
        restored_file_id = att.id
    else:  # archived / deleted：恢复整条资料（可能有文件，也可能无文件）
        if doc is None:
            raise AppError("NOT_FOUND", "关联资料不存在")
        # 归档时主文件与附件共用一条记录 → 恢复也必须两个一起（2026-11-06）
        if rec is not None and rec.status == "recycled":
            origin = e.origin_rel_path or f"documents/{rec.internal_name}"
            file_service.restore_recycled_file(
                db, file_record=rec, origin_rel_path=origin, actor=current_user
            )
        if att is not None and att.status == "recycled":
            file_service.restore_recycled_file(
                db,
                file_record=att,
                origin_rel_path=file_service.storage_rel_path(
                    "document", att.internal_name
                ),
                actor=current_user,
            )
        doc.archived_at = None
        doc.archived_by = None
        doc.archive_reason = None
        restored_file_id = rec.id if rec else None

    e.restored_at = datetime.now()
    e.restored_by = current_user.id

    # 恢复改变了资料的可见性（archived → active）：业务列表/工作台缓存必须失效，
    # 否则恢复后的资料最长 5 秒内仍可能从列表中消失（与 document_service.restore 一致）
    from app.core.cache import invalidate_lists

    invalidate_lists()

    audit_service.log(
        db,
        action=audit_service.ACTION_RECYCLE_RESTORE,
        user=current_user,
        object_type="recycle_bin",
        object_id=e.id,
        object_label=(rec.original_filename if rec else (doc.title if doc else f"#{e.id}")),
        detail={"reason": e.reason, "document_id": e.document_id,
                "file_record_id": rec.id if rec else None,
                "attachment_file_record_id": att.id if att else None,
                "reattached": e.reason == "attachment_deleted"},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return {
        "ok": True,
        "id": e.id,
        "status": "restored",
        "reason": e.reason,
        "restored_file_id": restored_file_id,
    }


def purge(
    db: Session,
    *,
    current_user: User,
    entry_id: int,
    confirm: bool = False,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """永久删除（仅 admin + confirm=true；§26 二次确认）。"""
    if not confirm:
        raise AppError("BAD_REQUEST", "永久删除不可恢复，必须传 confirm=true 二次确认")

    e = _load_entry(db, entry_id)
    rec = db.get(FileRecord, e.file_record_id) if e.file_record_id else None
    if e.file_record_id and rec is None:
        raise AppError("NOT_FOUND", "文件记录不存在")
    att = (
        db.get(FileRecord, e.attachment_file_record_id)
        if e.attachment_file_record_id
        else None
    )

    if rec is not None:
        file_service.purge_file(rec)  # 物理删除（唯一允许物理删文件的入口）
    # 2026-11-06：归档条目里可能带着附件，一并物理删除，否则留磁盘孤儿
    if att is not None:
        file_service.purge_file(att)
    e.purged_at = datetime.now()
    e.purged_by = current_user.id
    # FileRecord.status 只允许 active/recycled（DB CHECK），软标记落在 recycle_bin.purged_at

    audit_service.log(
        db,
        action=audit_service.ACTION_RECYCLE_PURGE,
        user=current_user,
        object_type="recycle_bin",
        object_id=e.id,
        object_label=rec.original_filename if rec else (
            att.original_filename if att else f"#{e.id}"
        ),
        detail={
            "file_record_id": e.file_record_id,
            "attachment_file_record_id": e.attachment_file_record_id,
            "purged": True,
        },
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return {"ok": True, "id": e.id, "status": "purged", "purged_at": e.purged_at}

def purge_all(
    db: Session,
    *,
    current_user: User,
    confirm: bool = False,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """清空回收站：物理删除全部在站文件条目与已删除通知（2026-08-30 新增）。

    逐条复用既有 purge 逻辑（文件经 file_service.purge_file 物理删、通知走
    purge_notice 彻底删）；完成后写一条汇总审计（via=purge_all），清空动作
    本身可追溯。前端单次确认覆盖整批（§26.1 精神：免多步确认）。

    2026-10-29 修复：此前这是**唯一没有后端 confirm 校验的物理删除入口**
    （单条 purge、单条通知 purge 都强制 confirm=true），仅靠前端弹窗。
    绕过前端直接调 API 即可一次性清空回收站，与 §26「必须明确确认」不符。
    """
    if not confirm:
        raise AppError(
            "BAD_REQUEST",
            "清空回收站会永久删除全部在站文件与已删除通知，必须传 confirm=true",
        )

    from app.services import notice_recycle

    entry_ids = db.execute(
        select(RecycleBin.id).where(
            RecycleBin.purged_at.is_(None), RecycleBin.restored_at.is_(None)
        )
    ).scalars().all()
    files_purged = 0
    for eid in entry_ids:
        try:
            purge(db, current_user=current_user, entry_id=eid, confirm=True,
                  ip=ip, user_agent=user_agent)
            files_purged += 1
        except AppError as exc:
            logger.warning("清空回收站：条目 %s 清理失败跳过（%s）", eid, exc)

    cancelled_ids = db.execute(
        select(Notice.id).where(Notice.status == "cancelled")
    ).scalars().all()
    notices_purged = 0
    for nid in cancelled_ids:
        try:
            notice_recycle.purge_notice(
                db, current_user=current_user, notice_id=nid, confirm=True
            )
            notices_purged += 1
        except AppError as exc:
            logger.warning("清空回收站：通知 %s 清理失败跳过（%s）", nid, exc)

    audit_service.log(
        db,
        action=audit_service.ACTION_RECYCLE_PURGE,
        user=current_user,
        object_type="recycle_bin",
        object_label="purge_all",
        detail={
            "via": "purge_all",
            "files_purged": files_purged,
            "notices_purged": notices_purged,
        },
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    from app.core.cache import invalidate_lists
    invalidate_lists()
    return {
        "ok": True,
        "files_purged": files_purged,
        "notices_purged": notices_purged,
    }
