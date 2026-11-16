"""认证路由（docs/API.md §1）。

- POST /api/v1/auth/login：匿名登录，成功签发 JWT + HttpOnly Cookie
- POST /api/v1/auth/logout：清 Cookie + 审计
- GET  /api/v1/auth/me：当前用户
- POST /api/v1/auth/change-password：修改本人密码
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import get_current_user
from app.core.security import create_access_token
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import ChangePasswordRequest, LoginRequest, LoginResponse
from app.services import auth_service, user_service

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

settings = get_settings()


def _set_auth_cookie(response: Response, token: str, *, clear: bool = False) -> None:
    """下发/清除 HttpOnly Cookie（DESIGN §4.1）。生产环境加 Secure。"""
    if clear:
        response.delete_cookie(
            "access_token", path="/", httponly=True, samesite="lax"
        )
        return
    max_age = settings.JWT_EXPIRE_MINUTES * 60
    response.set_cookie(
        key="access_token",
        value=token,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, response: Response,
          db: Session = Depends(get_db)):
    """登录：成功返回 token 并下发 HttpOnly Cookie（限速在 auth_service.login 内）。"""
    ip, ua = auth_service.client_info(request)
    result = auth_service.login(
        db, username=payload.username, password=payload.password, ip=ip, user_agent=ua
    )
    _set_auth_cookie(response, result["access_token"])
    return result


@router.post("/logout")
def logout(request: Request, response: Response,
           current_user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    """登出：清除 Cookie + 审计。"""
    ip, ua = auth_service.client_info(request)
    auth_service.logout(db, user=current_user, ip=ip, user_agent=ua)
    _set_auth_cookie(response, "", clear=True)
    return {"ok": True}


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    """当前用户信息（API.md §0.5 用户对象）。"""
    return user_service.user_to_dict(current_user)


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """修改本人密码（旧密码校验 + 强度校验）。"""
    ip, ua = auth_service.client_info(request)
    auth_service.change_password(
        db,
        user=current_user,
        old_password=payload.old_password,
        new_password=payload.new_password,
        ip=ip,
        user_agent=ua,
    )
    # 单点登录：改密自增了 token_version，其他设备已全部下线；
    # 当前设备用新 ver 重签令牌，避免把自己也踢回登录页
    token = create_access_token(
        user_id=current_user.id,
        username=current_user.username,
        role=current_user.role,
        token_version=current_user.token_version,
    )
    _set_auth_cookie(response, token)
    return {"ok": True}