"""认证服务（docs/API.md §1、DESIGN.md §4）。

- 登录：校验账号/密码/激活状态 → 签发 JWT → 审计 login / login_failed
- 登出：递增 token_version 使旧会话失效 → 审计 logout
- 修改密码：校验旧密码 + 强度 → 审计 change_password
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.common import validate_password_strength
from app.services import audit_service

settings = get_settings()


def _find_user(db: Session, username: str) -> Optional[User]:
    return db.execute(
        select(User).where(User.username == username, User.auth_source == "local")
    ).scalar_one_or_none()


def login(
    db: Session,
    *,
    username: str,
    password: str,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """登录：成功返回 token 信息；失败审计 login_failed 并抛 401/403（不区分用户不存在/密码错）。

    S5：登录限速在 service 层统一完成（每 IP 滑动窗口，防暴力破解）。
    """
    from app.core.security_middleware import login_rate_limiter
    login_rate_limiter.check(ip)
    try:
        user = _find_user(db, username)
    except Exception:
        login_rate_limiter.record(ip)
        raise

    if user is None or not verify_password(password, user.password_hash):
        login_rate_limiter.record(ip)  # 失败也记一次
        # 统一提示，避免账号枚举（API.md §1）
        audit_service.log(
            db,
            action=audit_service.ACTION_LOGIN_FAILED,
            username=username,
            detail={"reason": "invalid_credentials"},
            ip=ip,
            user_agent=user_agent,
            commit=True,
        )
        raise AppError("UNAUTHORIZED", "用户名或密码错误")

    if not user.is_active:
        audit_service.log(
            db,
            action=audit_service.ACTION_LOGIN_FAILED,
            user=user,
            detail={"reason": "disabled"},
            ip=ip,
            user_agent=user_agent,
            commit=True,
        )
        raise AppError("FORBIDDEN", "账号已停用，请联系管理员")

    login_rate_limiter.record(ip)  # 成功也记一次（重置窗口节奏）
    # 成功：更新 last_login_at + 审计（同一事务）
    user.last_login_at = datetime.now()
    # 单点登录（2026-09-03 产品决策）：每次登录自增 token_version → 旧令牌
    # 携带的 ver 与列值不等即 401，同账号旧设备全部下线
    user.token_version = (user.token_version or 0) + 1
    # 第一道防线：登录时补建缺失的阅读快照（幂等；发布后新用户/停用后启用的
    # 用户登录即修复，保证工作台计数与通知列表立即正确）
    from app.services.notice_audience import backfill_notice_reads

    backfill_notice_reads(db, user=user)
    token = create_access_token(
        user_id=user.id,
        username=user.username,
        role=user.role,
        token_version=user.token_version,
    )
    audit_service.log(
        db,
        action=audit_service.ACTION_LOGIN,
        user=user,
        detail={"login_from": "local"},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.JWT_EXPIRE_MINUTES * 60,
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
        },
        "must_change_password": bool(user.must_change_password),
    }


def logout(
    db: Session,
    *,
    user: User,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """登出：递增 token_version 使旧 Bearer/Cookie 全部失效，并审计。"""
    user.token_version = (user.token_version or 0) + 1
    audit_service.log(
        db,
        action=audit_service.ACTION_LOGOUT,
        user=user,
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()


def change_password(
    db: Session,
    *,
    user: User,
    old_password: str,
    new_password: str,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> int:
    """修改本人密码：校验旧密码（错 → 401）与强度（不足 → 400）。

    返回自增后的 token_version：单点登录下其他设备全部下线；当前设备由
    路由层用新 ver 重签令牌（避免把自己也踢下线）。
    """
    if not verify_password(old_password, user.password_hash):
        raise AppError("UNAUTHORIZED", "原密码不正确")

    validate_password_strength(new_password)

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.token_version = (user.token_version or 0) + 1
    audit_service.log(
        db,
        action=audit_service.ACTION_CHANGE_PASSWORD,
        user=user,
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return user.token_version


def client_info(request) -> tuple[Optional[str], Optional[str]]:
    """从请求提取客户端 IP 与 UA（日志用）。

    L-3 修复：支持反代头 X-Forwarded-For（内网部署常见场景）。
    S4 修复：仅当 TRUST_REVERSE_PROXY_HEADERS=true 才信任反代头，
    否则直接连接场景下恶意请求可伪造 IP 绕过登录限速。
    """
    from app.core.config import get_settings
    ip = None
    if request.client:
        ip = request.client.host
    settings = get_settings()
    if settings.TRUST_REVERSE_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            ip = forwarded.split(",")[0].strip() or ip
    ua = request.headers.get("user-agent")
    return ip, ua