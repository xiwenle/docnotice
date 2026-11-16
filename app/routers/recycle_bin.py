"""回收站路由（docs/API.md §8）。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.db.session import get_db
from app.models.user import User
from app.services import auth_service, notice_recycle, recycle_service

router = APIRouter(prefix="/api/v1/recycle-bin", tags=["recycle-bin"])

admin_only = require_roles("admin")  # 权限模型 v3：管理操作仅管理员


class PurgeRequest(BaseModel):
    confirm: bool = False


class BulkNoticeRestoreRequest(BaseModel):
    notice_ids: list[int]


# ---------------------------------------------------------------------------
# 已删除通知（回收站）：列表 / 恢复 / 彻底删除 —— 全部仅管理员
# ---------------------------------------------------------------------------
@router.get("/notices")
def list_deleted_notices(
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """回收站-已删除通知列表（cancelled）。"""
    return notice_recycle.list_deleted_notices(db)


@router.post("/notices/bulk-restore")
def bulk_restore_deleted_notices(
    payload: BulkNoticeRestoreRequest,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """批量恢复已删除通知（仅管理员；逐条返回成功/跳过原因 + 汇总审计）。"""
    ip, ua = auth_service.client_info(request)
    return notice_recycle.bulk_restore_notices(
        db,
        current_user=current_user,
        notice_ids=payload.notice_ids,
        ip=ip,
        user_agent=ua,
    )


@router.post("/notices/{notice_id}/restore")
def restore_deleted_notice(
    notice_id: int,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """恢复已删除通知（草稿恢复为草稿；已发布恢复为 published，保留阅读统计）。"""
    ip, ua = auth_service.client_info(request)
    return notice_recycle.restore_notice(
        db, current_user=current_user, notice_id=notice_id, ip=ip, user_agent=ua
    )


@router.delete("/notices/{notice_id}")
def purge_deleted_notice(
    notice_id: int,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
    payload: Optional[PurgeRequest] = None,
):
    """彻底删除回收站通知（物理删除；confirm=true 二次确认，§26）。"""
    ip, ua = auth_service.client_info(request)
    return notice_recycle.purge_notice(
        db, current_user=current_user, notice_id=notice_id,
        confirm=bool(payload and payload.confirm), ip=ip, user_agent=ua,
    )


@router.get("")
def list_recycle_bin(
    reason: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """回收站列表（admin+）。"""
    return recycle_service.list_recycle(
        db, reason=reason, page=page, page_size=page_size
    )


@router.post("/{entry_id}/restore")
def restore_entry(
    entry_id: int,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """恢复（admin+；replaced 回文件库，archived/deleted 恢复整条资料）。"""
    ip, ua = auth_service.client_info(request)
    return recycle_service.restore(
        db, current_user=current_user, entry_id=entry_id, ip=ip, user_agent=ua
    )


@router.delete("/{entry_id}")
def purge_entry(
    entry_id: int,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
    payload: Optional[PurgeRequest] = None,
):
    """永久删除（仅 admin；confirm=true 二次确认，缺省/缺失 body → 400，§26 唯一物理删除入口）。"""
    ip, ua = auth_service.client_info(request)
    return recycle_service.purge(
        db, current_user=current_user, entry_id=entry_id,
        confirm=bool(payload and payload.confirm), ip=ip, user_agent=ua,
    )

@router.post("/purge-all")
def purge_all(
    request: Request,
    payload: Optional[PurgeRequest] = None,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """清空回收站：物理删除全部在站文件条目与已删除通知（admin；须 confirm=true）。

    2026-10-29：后端补齐 confirm 校验。此前只靠前端弹窗，绕过界面直接调 API
    即可一次性清空回收站，与 §26「必须明确确认」及同类端点（单条 purge 均
    强制 confirm=true）不一致。
    """
    ip, ua = auth_service.client_info(request)
    result = recycle_service.purge_all(
        db, current_user=current_user,
        confirm=bool(payload and payload.confirm), ip=ip, user_agent=ua,
    )
    from app.core.cache import invalidate_lists
    invalidate_lists()
    return result
