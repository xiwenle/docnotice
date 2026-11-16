"""操作日志路由（docs/API.md §9，admin+）。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.core.errors import AppError
from app.db.session import get_db
from app.models.user import User
from app.services import audit_service, auth_service

router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit-logs"])

admin_only = require_roles("admin")  # 权限模型 v3：管理操作仅管理员


@router.get("")
def list_logs(
    user_id: Optional[int] = Query(default=None),
    username: Optional[str] = Query(default=None),
    action: Optional[str] = Query(default=None),
    object_type: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """日志查询（admin+；普通用户一律 403）。"""
    return audit_service.query_logs(
        db,
        user_id=user_id,
        username=username,
        action=action,
        object_type=object_type,
        date_from=date_from,
        date_to=date_to,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@router.get("/{log_id}")
def get_log(
    log_id: int,
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """单条日志（admin+）。"""
    from sqlalchemy import select

    row = db.execute(
        select(audit_service.AuditLog).where(audit_service.AuditLog.id == log_id)
    ).scalar_one_or_none()
    if row is None:
        raise AppError("NOT_FOUND", "日志不存在")
    return {
        "id": row.id,
        "user_id": row.user_id,
        "username": row.username,
        "action": row.action,
        "object_type": row.object_type,
        "object_id": row.object_id,
        "object_label": row.object_label,
        "detail": row.detail,
        "ip": row.ip,
        "user_agent": row.user_agent,
        "created_at": row.created_at,
    }

class AuditBatchDeleteRequest(BaseModel):
    ids: list[int]


@router.delete("/{log_id}")
def delete_audit_log(
    log_id: int,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """删除单条操作日志（物理删；删除动作本身写汇总审计 audit_delete）。"""
    ip, ua = auth_service.client_info(request)
    return audit_service.delete_logs(
        db, current_user=current_user, ids=[log_id], ip=ip, user_agent=ua
    )


@router.post("/batch-delete")
def batch_delete_audit_logs(
    payload: AuditBatchDeleteRequest,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """批量删除操作日志（勾选行；单次确认覆盖整批；写汇总审计）。"""
    ip, ua = auth_service.client_info(request)
    return audit_service.delete_logs(
        db, current_user=current_user, ids=payload.ids, ip=ip, user_agent=ua
    )
