"""认证相关 Schema（docs/API.md §1）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class UserBrief(BaseModel):
    """登录响应中的用户摘要（API.md §1）。"""

    id: int
    username: str
    display_name: str
    role: str


class LoginRequest(BaseModel):
    """POST /api/v1/auth/login 请求体。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(BaseModel):
    """登录成功响应。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserBrief
    # 首登/重置密码后为 True：前端据此强制跳转改密页（配合 /change-password 页面）
    must_change_password: bool = False


class ChangePasswordRequest(BaseModel):
    """修改本人密码。"""

    old_password: str
    new_password: str