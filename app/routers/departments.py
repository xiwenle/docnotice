"""部门路由（docs/API.md §2）。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.db.session import get_db
from app.models.user import User
from app.schemas.department import DepartmentCreate, DepartmentUpdate
from app.services import auth_service, department_service

router = APIRouter(prefix="/api/v1/departments", tags=["departments"])

admin_only = require_roles("admin")  # 权限模型 v3：管理操作仅管理员


@router.get("")
def list_departments(
    is_active: Optional[bool] = Query(default=None),
    ids: Optional[list[int]] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    with_member_count: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=100),
    _: User = Depends(require_roles("user", "admin")),
    db: Session = Depends(get_db),
):
    """部门列表（登录用户可读）。"""
    return department_service.list_departments(
        db,
        is_active=is_active,
        ids=ids,
        keyword=keyword,
        with_member_count=with_member_count,
        page=page,
        page_size=page_size,
    )


@router.post("", status_code=201)
def create_department(
    payload: DepartmentCreate,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """新建部门（admin+）。"""
    ip, ua = auth_service.client_info(request)
    dep = department_service.create_department(
        db,
        current_user=current_user,
        name=payload.name,
        description=payload.description,
        ip=ip,
        user_agent=ua,
    )
    return {"id": dep.id, "name": dep.name,
            "description": dep.description, "is_active": bool(dep.is_active)}


@router.put("/{dep_id}")
def update_department(
    dep_id: int,
    payload: DepartmentUpdate,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """修改部门（admin+）。"""
    ip, ua = auth_service.client_info(request)
    dep = department_service.update_department(
        db,
        current_user=current_user,
        dep_id=dep_id,
        name=payload.name,
        description=payload.description,
        ip=ip,
        user_agent=ua,
    )
    return {"id": dep.id, "name": dep.name,
            "description": dep.description, "is_active": bool(dep.is_active)}


@router.delete("/{dep_id}")
def delete_department(
    dep_id: int,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """删除部门（admin+；成员变未分配部门，通知部门目标摘除；用户/通知不删除）。"""
    ip, ua = auth_service.client_info(request)
    return department_service.delete_department(
        db, current_user=current_user, dep_id=dep_id, ip=ip, user_agent=ua,
    )