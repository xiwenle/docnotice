"""分类路由（docs/API.md §4）。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_roles
from app.db.session import get_db
from app.models.user import User
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.services import auth_service, category_service

router = APIRouter(prefix="/api/v1/categories", tags=["categories"])

admin_only = require_roles("admin")  # 权限模型 v3：管理操作仅管理员


@router.get("")
def list_categories(
    parent_id: Optional[int] = Query(default=None),
    with_document_count: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """分类列表（树/扁平；登录用户可读）。

    document_count 按当前用户可见范围统计（2026-11-04）：普通用户看到的
    数字必须等于点进该分类能列出的资料数。
    """
    return category_service.list_categories(
        db,
        parent_id=parent_id,
        with_document_count=with_document_count,
        user=current_user,
    )


@router.post("", status_code=201)
def create_category(
    payload: CategoryCreate,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """新建分类（admin+；深度 ≤4、同级重名检查）。"""
    ip, ua = auth_service.client_info(request)
    cat = category_service.create_category(
        db,
        current_user=current_user,
        name=payload.name,
        parent_id=payload.parent_id,
        sort_order=payload.sort_order,
        ip=ip,
        user_agent=ua,
    )
    return {"id": cat.id, "name": cat.name, "parent_id": cat.parent_id,
            "sort_order": cat.sort_order}


@router.put("/{category_id}")
def update_category(
    category_id: int,
    payload: CategoryUpdate,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """修改分类（admin+；深度≤4、不成环）。"""
    ip, ua = auth_service.client_info(request)
    cat = category_service.update_category(
        db,
        current_user=current_user,
        category_id=category_id,
        name=payload.name,
        parent_id=payload.parent_id,
        sort_order=payload.sort_order,
        ip=ip,
        user_agent=ua,
    )
    return {"id": cat.id, "name": cat.name, "parent_id": cat.parent_id,
            "sort_order": cat.sort_order, "is_active": bool(cat.is_active)}


@router.delete("/{category_id}")
def delete_category(
    category_id: int,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """删除分类（admin+；2026-11-09 用户确认：删除整棵子树，
    树内资料含已归档变未分类；免二次确认；返回删除分类数与未分类资料数）。"""
    ip, ua = auth_service.client_info(request)
    return category_service.delete_category(
        db, current_user=current_user, category_id=category_id, ip=ip, user_agent=ua
    )