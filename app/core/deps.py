"""认证与 RBAC 依赖（docs/DESIGN.md §4.2、API.md §0.1、项目规范 §33）。

- get_current_user：本地账号认证——解析 Bearer 头或 HttpOnly Cookie 中的本地 JWT，
  加载用户并复核激活状态
- require_roles(...)：角色白名单依赖工厂；管理端点一律 require_roles("admin")
- 权限由后端强制校验，前端隐藏按钮不作为权限依据（§33）
"""
from __future__ import annotations

from typing import Callable

from fastapi import Depends, Request
from fastapi.security.utils import get_authorization_scheme_param
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

# 权限模型 v3：两角色（user / admin）；superadmin 已并入 admin（2026-08-29）
ADMIN_ROLES = ("admin",)

settings = get_settings()

# 首登/重置改密闸门白名单（2026-10-16 P0 修复）：
# must_change_password=True 的用户仅允许调用这三个 API——
# 查自身（前端守卫据 /auth/me 的标记跳转）、改密本体、登出。
# 其余 /api/ 一律 403 PASSWORD_CHANGE_REQUIRED，封死"持 JWT 直连 API 绕过改密"的路径。
# 注意：闸门只拦 /api/ 前缀——页面路由（pages.py::_resolve_user）会吞 AppError
# 渲染为匿名态，若拦页面会把已登录用户弹回登录页并打断 /change-password 自身渲染；
# 浏览器侧的强制跳转由 base.html meta refresh 承担，此处只补 API 侧的洞。
_PASSWORD_CHANGE_ALLOWED = frozenset(
    {
        "/api/v1/auth/me",
        "/api/v1/auth/change-password",
        "/api/v1/auth/logout",
    }
)




def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """当前登录用户依赖（本地 JWT）。"""

    token = None

    auth_header = request.headers.get("Authorization")
    if auth_header:
        scheme, param = get_authorization_scheme_param(auth_header)
        if scheme.lower() == "bearer" and param:
            token = param
    if token is None:
        token = request.cookies.get("access_token")

    if not token:
        raise AppError("UNAUTHORIZED", "未登录或登录已失效")

    payload = decode_access_token(token)
    user_id = payload.get("user_id") or payload.get("sub")
    if user_id is None:
        raise AppError("UNAUTHORIZED", "无效的登录凭证")

    user = db.get(User, int(user_id))
    if user is None or user.auth_source != "local":
        raise AppError("UNAUTHORIZED", "账号不存在或已注销")
    if not user.is_active:
        raise AppError("FORBIDDEN", "账号已停用，请联系管理员")

    # 令牌中的角色与数据库复核：角色变更后旧令牌立即失效对应权限
    if payload.get("role") != user.role:
        raise AppError("UNAUTHORIZED", "登录凭证已失效，请重新登录")

    # 单点登录复核（2026-09-03）：令牌 ver 与列值不等 = 已被新登录/改密/重置
    # 顶掉 → 401，旧设备下次请求即回到登录页
    if (payload.get("ver") or 0) != (user.token_version or 0):
        raise AppError("UNAUTHORIZED", "账号已在其他设备登录，请重新登录")

    # 首登/重置改密闸门（2026-10-16 P0 修复）：标记未清时禁止调用业务 API。
    # 白名单外的 /api/ 路径一律 403；页面路由不拦（见 _PASSWORD_CHANGE_ALLOWED 注释）。
    if user.must_change_password:
        path = request.url.path
        if path.startswith("/api/") and path not in _PASSWORD_CHANGE_ALLOWED:
            raise AppError(
                "PASSWORD_CHANGE_REQUIRED",
                "首次登录或密码重置后必须修改密码，才能继续使用系统",
            )

    return user


def require_roles(*roles: str) -> Callable:
    """角色白名单依赖工厂：当前用户角色不在白名单 → 403 FORBIDDEN。"""

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise AppError("FORBIDDEN", "没有权限执行此操作")
        return current_user

    return dependency


# 便捷别名
requires_user = get_current_user
requires_admin = require_roles(*ADMIN_ROLES)