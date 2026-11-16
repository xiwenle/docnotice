"""公共 Schema：分页响应结构与密码强度校验。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    """列表接口统一分页响应：{"items": [...], "total", "page", "page_size", "pages"}。"""

    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


class PasswordStrengthError(ValueError):
    """密码强度不足（内部使用，由服务层转换为 400 VALIDATION_ERROR）。"""


def validate_password_strength(password: str) -> None:
    """新密码强度校验：≥8 位，且同时包含字母与数字（docs/API.md §1）。"""
    if len(password) < 8:
        raise PasswordStrengthError("密码长度至少 8 位")
    has_alpha = any(ch.isalpha() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)
    if not (has_alpha and has_digit):
        raise PasswordStrengthError("密码必须同时包含字母和数字")


def page_meta(total: int, page: int, page_size: int) -> dict[str, Any]:
    """计算分页元信息。"""
    pages = (total + page_size - 1) // page_size if page_size > 0 else 0
    return {"total": total, "page": page, "page_size": page_size, "pages": pages}