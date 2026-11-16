"""备份/恢复路由（docs/API.md §10；恢复/删除仅 admin + confirm）。"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.db.session import get_db
from app.models.user import User
from app.services import auth_service, backup_service

router = APIRouter(prefix="/api/v1/backups", tags=["backups"])

admin_only = require_roles("admin")  # 权限模型 v3：管理操作仅管理员


class BackupCreateRequest(BaseModel):
    type: str = "full"  # full=库+文件（默认）；database=仅库


class RestoreRequest(BaseModel):
    confirm: bool = False
    reason: Optional[str] = Field(default=None, max_length=255)


class DeleteRequest(BaseModel):
    confirm: bool = False


@router.post("", status_code=201)
def create_backup(
    payload: BackupCreateRequest,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """新建备份（admin+；同步执行）。"""
    ip, ua = auth_service.client_info(request)
    record = backup_service.create_backup(
        db, current_user=current_user, backup_type=payload.type,
        note="手动备份", ip=ip, user_agent=ua,
    )
    # 保留策略清理（失败不影响本次备份结果，下次动作自动重试）
    try:
        backup_service.apply_retention(db, current_user=current_user, ip=ip, user_agent=ua)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("备份后保留清理失败", exc_info=True)
    return {
        "id": record.id,
        "backup_type": record.backup_type,
        "backup_path": record.backup_path,
        "size_bytes": record.size_bytes,
        "file_count": record.file_count,
        "db_sha256": record.db_sha256,
        "status": record.status,
        "source": record.source,
        "created_at": record.created_at,
    }


@router.get("")
def list_backups(
    type: Optional[str] = Query(default=None, alias="type"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """备份列表（admin+；含保留策略回显 policy）。"""
    result = backup_service.list_backups(
        db, backup_type=type, page=page, page_size=page_size
    )
    result["policy"] = backup_service.retention_policy()
    return result


@router.get("/{backup_id}")
def get_backup(
    backup_id: int,
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """备份详情（含 manifest 摘要）。"""
    return backup_service.get_backup(db, backup_id)


@router.post("/{backup_id}/verify")
def verify_backup(
    backup_id: int,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """校验备份完整性（admin-only；只读校验，不恢复）。"""
    ip, ua = auth_service.client_info(request)
    return backup_service.verify_backup(
        db, current_user=current_user, backup_id=backup_id, ip=ip, user_agent=ua
    )


@router.post("/{backup_id}/restore")
def restore_backup(
    backup_id: int,
    request: Request,
    payload: Optional[RestoreRequest] = None,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """恢复备份（仅 admin；confirm=true 二次确认，缺省/缺失 body → 400）。"""
    ip, ua = auth_service.client_info(request)
    return backup_service.restore_backup(
        db, current_user=current_user, backup_id=backup_id,
        confirm=bool(payload and payload.confirm),
        reason=payload.reason if payload else None, ip=ip, user_agent=ua,
    )


@router.delete("/{backup_id}")
def delete_backup(
    backup_id: int,
    request: Request,
    payload: Optional[DeleteRequest] = None,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """删除备份（仅 admin；confirm=true 二次确认）。"""
    ip, ua = auth_service.client_info(request)
    return backup_service.delete_backup(
        db, current_user=current_user, backup_id=backup_id,
        confirm=bool(payload and payload.confirm), ip=ip, user_agent=ua,
    )