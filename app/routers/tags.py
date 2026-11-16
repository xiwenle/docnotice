"""标签路由（docs/API.md §5）。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_roles
from app.db.session import get_db
from app.models.user import User
from app.schemas.tag import TagCreate, TagUpdate
from app.services import auth_service, tag_service

router = APIRouter(prefix="/api/v1/tags", tags=["tags"])

admin_only = require_roles("admin")  # 权限模型 v3：管理操作仅管理员


@router.get("")
def list_tags(
    keyword: Optional[str] = Query(default=None),
    with_count: bool = Query(default=False),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """标签列表（登录用户可读）。"""
    return tag_service.list_tags(db, keyword=keyword, with_count=with_count)


@router.post("", status_code=201)
def create_tag(
    payload: TagCreate,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """新建标签（admin+；重名 409 DUPLICATE）。"""
    ip, ua = auth_service.client_info(request)
    tag = tag_service.create_tag(
        db, current_user=current_user, name=payload.name, ip=ip, user_agent=ua
    )
    return {"id": tag.id, "name": tag.name}


@router.put("/{tag_id}")
def update_tag(
    tag_id: int,
    payload: TagUpdate,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """修改标签（admin+）。"""
    ip, ua = auth_service.client_info(request)
    tag = tag_service.update_tag(
        db, current_user=current_user, tag_id=tag_id, name=payload.name, ip=ip, user_agent=ua
    )
    return {"id": tag.id, "name": tag.name}


@router.delete("/{tag_id}")
def delete_tag(
    tag_id: int,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """删除标签（admin+；2026-11-09 用户确认：只解除资料关联，资料变未打标签）。"""
    ip, ua = auth_service.client_info(request)
    return tag_service.delete_tag(
        db, current_user=current_user, tag_id=tag_id, ip=ip, user_agent=ua
    )