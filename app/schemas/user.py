"""用户管理 Schema（docs/API.md §0.5、§3）。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

VALID_ROLES = ("user", "admin")


class UserOut(BaseModel):
    """用户对象（API.md §0.5）。"""

    id: int
    username: str
    display_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    role: str
    is_active: bool
    department_ids: list[int] = []
    must_change_password: bool = False
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class UserCreate(BaseModel):
    """POST /api/v1/users 请求体。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    role: str = "user"
    email: Optional[str] = Field(default=None, max_length=128)
    department_ids: list[int] = []
    must_change_password: bool = True


class UserUpdate(BaseModel):
    """PUT /api/v1/users/{id} 请求体（可部分）。"""

    username: Optional[str] = Field(default=None, max_length=64)
    display_name: Optional[str] = Field(default=None, max_length=128)
    email: Optional[str] = Field(default=None, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=32)
    role: Optional[str] = None
    is_active: Optional[bool] = None
    department_ids: Optional[list[int]] = None


class ResetPasswordRequest(BaseModel):
    """重置密码。"""

    new_password: str = Field(min_length=8, max_length=128)