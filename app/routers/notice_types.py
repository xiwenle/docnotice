"""通知类型路由（可自定义类型词表，admin 管理；登录用户可读）。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_roles
from app.db.session import get_db
from app.models.user import User
from app.schemas.notice_type import NoticeTypeCreate, NoticeTypeUpdate
from app.services import auth_service, notice_type_service

router = APIRouter(prefix="/api/v1/notice-types", tags=["notice-types"])

admin_only = require_roles("admin")  # 权限模型 v3：管理操作仅管理员


@router.get("")
def list_notice_types(
    keyword: Optional[str] = Query(default=None),
    with_count: bool = Query(default=False),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """通知类型列表（登录用户可读，供表单下拉/列表筛选）。"""
    return notice_type_service.list_notice_types(
        db, keyword=keyword, with_count=with_count
    )


@router.post("", status_code=201)
def create_notice_type(
    payload: NoticeTypeCreate,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """新建通知类型（admin+；同名 409 DUPLICATE）。"""
    ip, ua = auth_service.client_info(request)
    nt = notice_type_service.create_notice_type(
        db,
        current_user=current_user,
        name=payload.name,
        sort_order=payload.sort_order,
        ip=ip,
        user_agent=ua,
    )
    return {"id": nt.id, "name": nt.name, "sort_order": nt.sort_order,
            "is_active": bool(nt.is_active)}


@router.put("/{notice_type_id}")
def update_notice_type(
    notice_type_id: int,
    payload: NoticeTypeUpdate,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """修改通知类型（admin+）。"""
    ip, ua = auth_service.client_info(request)
    nt = notice_type_service.update_notice_type(
        db,
        current_user=current_user,
        notice_type_id=notice_type_id,
        name=payload.name,
        sort_order=payload.sort_order,
        ip=ip,
        user_agent=ua,
    )
    return {"id": nt.id, "name": nt.name, "sort_order": nt.sort_order,
            "is_active": bool(nt.is_active)}


@router.delete("/{notice_type_id}")
def delete_notice_type(
    notice_type_id: int,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """删除通知类型（admin+；普通类型删除后通知变未分类；废止/替换禁止删除）。"""
    ip, ua = auth_service.client_info(request)
    return notice_type_service.delete_notice_type(
        db,
        current_user=current_user,
        notice_type_id=notice_type_id,
        ip=ip,
        user_agent=ua,
    )