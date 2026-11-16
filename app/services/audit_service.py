"""操作日志服务（docs/DESIGN.md §9、项目规范 §25）。

- 与业务操作同一事务写入（登录失败等无事务场景单独调用 commit）
- action 常量表（DESIGN §9 第一版必需覆盖全集）
"""
from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.session import total_count
from app.models.audit import AuditLog
from app.models.user import User

# action 常量表（DESIGN §9）
ACTION_LOGIN = "login"
ACTION_LOGIN_FAILED = "login_failed"
ACTION_LOGOUT = "logout"
ACTION_CHANGE_PASSWORD = "change_password"
ACTION_MANAGE_USER = "manage_user"
ACTION_CREATE_DEPARTMENT = "create_department"
ACTION_UPDATE_DEPARTMENT = "update_department"
ACTION_DELETE_DEPARTMENT = "delete_department"
ACTION_CREATE_DOCUMENT = "create_document"
ACTION_UPDATE_DOCUMENT = "update_document"
ACTION_REPLACE_FILE = "replace_file"
# 2026-11-06：资料附件（不预览的第二文件槽）
ACTION_SET_ATTACHMENT = "set_document_attachment"
ACTION_DELETE_ATTACHMENT = "delete_document_attachment"
ACTION_DELETE_DOCUMENT = "delete_document"
ACTION_ARCHIVE_DOCUMENT = "archive_document"
ACTION_RESTORE_DOCUMENT = "restore_document"
ACTION_MODIFY_VALIDITY = "modify_validity"
ACTION_CREATE_NOTICE = "create_notice"
ACTION_UPDATE_NOTICE = "update_notice"
ACTION_PUBLISH_NOTICE = "publish_notice"
ACTION_CANCEL_NOTICE = "cancel_notice"
ACTION_READ_NOTICE = "read_notice"
ACTION_CONFIRM_NOTICE = "confirm_notice"
ACTION_CREATE_CATEGORY = "create_category"
ACTION_UPDATE_CATEGORY = "update_category"
ACTION_DELETE_CATEGORY = "delete_category"
ACTION_CREATE_TAG = "create_tag"
ACTION_UPDATE_TAG = "update_tag"
ACTION_DELETE_TAG = "delete_tag"
ACTION_CREATE_NOTICE_TYPE = "create_notice_type"
ACTION_UPDATE_NOTICE_TYPE = "update_notice_type"
ACTION_DELETE_NOTICE_TYPE = "delete_notice_type"
ACTION_BACKUP_CREATE = "backup_create"
ACTION_BACKUP_RESTORE = "backup_restore"
ACTION_BACKUP_DELETE = "backup_delete"
ACTION_BACKUP_RECONCILE = "backup_reconcile"
ACTION_BACKUP_VERIFY = "backup_verify"
ACTION_RECYCLE_RESTORE = "recycle_restore"
ACTION_RECYCLE_PURGE = "recycle_purge"
ACTION_AUDIT_DELETE = "audit_delete"

# 全部 action 常量（供校验/文档使用）
ALL_ACTIONS = tuple(
    v
    for k, v in sorted(globals().items())
    if k.startswith("ACTION_") and isinstance(v, str)
)


def log(
    db: Session,
    *,
    action: str,
    user: Optional[User] = None,
    username: Optional[str] = None,
    object_type: Optional[str] = None,
    object_id: Optional[int] = None,
    object_label: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    commit: bool = False,
) -> AuditLog:
    """写入一条操作日志。

    - user 传入时自动快照 user.username；也可单独传 username（如登录失败/系统动作）
    - commit=False：与业务操作同事务（由调用方统一 commit）；
      commit=True：独立写入并提交（登录失败等无业务事务的场景）
    """
    entry = AuditLog(
        user_id=user.id if user else None,
        username=username if username is not None else (user.username if user else None),
        action=action,
        object_type=object_type,
        object_id=object_id,
        object_label=object_label,
        detail=json.dumps(detail, ensure_ascii=False) if detail is not None else None,
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
    )
    db.add(entry)
    if commit:
        db.commit()
        db.refresh(entry)
    return entry

def query_logs(
    db: Session,
    *,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    action: Optional[str] = None,
    object_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """操作日志查询（API.md §9，admin+）。"""
    from datetime import datetime, time, timedelta

    from sqlalchemy import or_, select

    stmt = select(AuditLog).order_by(AuditLog.id.desc())
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if username and username.strip():
        # 操作人搜索走服务端：账号快照 + 当前用户姓名；已删用户仍可按快照账号检索。
        operator_like = f"%{username.strip()}%"
        stmt = stmt.where(
            or_(
                AuditLog.username.like(operator_like),
                AuditLog.user.has(User.display_name.like(operator_like)),
            )
        )
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if object_type:
        stmt = stmt.where(AuditLog.object_type == object_type)
    if date_from:
        try:
            stmt = stmt.where(AuditLog.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            raise AppError("VALIDATION_ERROR", "date_from 格式错误（ISO8601）")
    if date_to:
        try:
            to_at = datetime.fromisoformat(date_to)
        except ValueError:
            raise AppError("VALIDATION_ERROR", "date_to 格式错误（ISO8601）")
        if to_at.time() == time(0, 0):
            # 前端日期控件只给日期：按"含当日全天"处理（与通知列表同口径）
            stmt = stmt.where(AuditLog.created_at < to_at + timedelta(days=1))
        else:
            stmt = stmt.where(AuditLog.created_at <= to_at)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(AuditLog.object_label.like(like), AuditLog.detail.like(like)))

    total = total_count(db, stmt)
    rows = db.execute(
        stmt.offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    items = [
        {
            "id": r.id,
            "user_id": r.user_id,
            "username": r.username,
            "action": r.action,
            "object_type": r.object_type,
            "object_id": r.object_id,
            "object_label": r.object_label,
            "detail": r.detail,
            "ip": r.ip,
            "user_agent": r.user_agent,
            "created_at": r.created_at,
        }
        for r in rows
    ]
    from app.schemas.common import page_meta as _pm

    return {"items": items, **_pm(total, page, page_size)}


# ---------------------------------------------------------------------------
# 操作日志删除（2026-08-30 拍板：日志可删；删除动作本身留汇总审计）
# ---------------------------------------------------------------------------
def delete_logs(
    db: Session,
    *,
    current_user: User,
    ids: list[int],
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """物理删除操作日志（去重、不存在的 id 跳过）。

    删除动作写一条汇总审计（audit_delete，含被删 id 清单与操作人）——
    旧日志没了，但"谁在何时删了哪些日志"永远可追溯，非无痕删除。
    """
    clean = sorted({int(i) for i in ids})
    if not clean:
        raise AppError("VALIDATION_ERROR", "ids 不能为空")
    rows = db.execute(select(AuditLog).where(AuditLog.id.in_(clean))).scalars().all()
    deleted = [r.id for r in rows]
    for r in rows:
        db.delete(r)
    log(
        db,
        action=ACTION_AUDIT_DELETE,
        user=current_user,
        object_type="audit_log",
        object_label=f"删除 {len(deleted)} 条",
        detail={"deleted_ids": deleted, "requested": len(clean)},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return {"ok": True, "deleted": deleted, "missing": sorted(set(clean) - set(deleted))}
