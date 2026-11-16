"""用户管理路由（docs/API.md §3，均 admin+ 且后端强制校验）。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import ResetPasswordRequest, UserCreate, UserUpdate
from app.services import auth_service, user_service

router = APIRouter(prefix="/api/v1/users", tags=["users"])

# 权限模型 v3：两角色，用户管理（含角色授予）仅 admin；
# 自我保护规则（不能停用自己/改自己角色/停用最后一个 admin）在服务层。
admin_only = require_roles("admin")


@router.get("")
def list_users(
    role: Optional[str] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    department_id: Optional[int] = Query(default=None),
    ids: Optional[list[int]] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """用户列表（分页 + 筛选）。"""
    return user_service.list_users(
        db,
        role=role,
        is_active=is_active,
        department_id=department_id,
        ids=ids,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@router.post("", status_code=201)
def create_user(
    payload: UserCreate,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """新建用户（角色仅 user/admin，授予 admin 即管理员）。"""
    ip, ua = auth_service.client_info(request)
    user = user_service.create_user(
        db,
        current_user=current_user,
        username=payload.username,
        password=payload.password,
        display_name=payload.display_name,
        role=payload.role,
        email=payload.email,
        department_ids=payload.department_ids,
        must_change_password=payload.must_change_password,
        ip=ip,
        user_agent=ua,
    )
    return user_service.user_to_dict(user)


@router.get("/{user_id}")
def get_user_detail(
    user_id: int,
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """用户详情（含部门列表）。"""
    user = user_service.get_user(db, user_id)
    return user_service.user_to_dict(user, include_departments=True)


@router.put("/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """修改用户（角色/停用含保护规则）。"""
    ip, ua = auth_service.client_info(request)
    user = user_service.update_user(
        db,
        current_user=current_user,
        target_id=user_id,
        username=payload.username,
        display_name=payload.display_name,
        email=payload.email,
        phone=payload.phone,
        role=payload.role,
        is_active=payload.is_active,
        department_ids=payload.department_ids,
        ip=ip,
        user_agent=ua,
    )
    return user_service.user_to_dict(user)


@router.delete("/{user_id}")
def disable_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """停用用户（软删除，§26）。"""
    ip, ua = auth_service.client_info(request)
    user = user_service.disable_user(
        db, current_user=current_user, target_id=user_id, ip=ip, user_agent=ua
    )
    return user_service.user_to_dict(user)


@router.delete("/{user_id}/purge")
def purge_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """删除用户（物理删除；创建过内容的账号 409；产品决策 2026-09 无需二次确认）。"""
    ip, ua = auth_service.client_info(request)
    return user_service.purge_user(
        db, current_user=current_user, target_id=user_id, ip=ip, user_agent=ua,
    )


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int,
    payload: ResetPasswordRequest,
    request: Request,
    current_user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """重置密码（成功后置 must_change_password=1）。"""
    ip, ua = auth_service.client_info(request)
    user = user_service.reset_password(
        db,
        current_user=current_user,
        target_id=user_id,
        new_password=payload.new_password,
        ip=ip,
        user_agent=ua,
    )
    return {"ok": True, "username": user.username, "must_change_password": True}