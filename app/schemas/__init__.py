"""Pydantic 请求/响应模型（按 docs/API.md）。

当前覆盖：auth / user / department / category / tag / document。
"""
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    UserBrief,
)
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.schemas.common import (
    PageResult,
    PasswordStrengthError,
    page_meta,
    validate_password_strength,
)
from app.schemas.department import (
    DepartmentCreate,
    DepartmentOut,
    DepartmentUpdate,
)
from app.schemas.document import (
    ArchiveRequest,
    DocumentCreate,
    DocumentUpdate,
    RestoreRequest,
)
from app.schemas.tag import TagCreate, TagUpdate
from app.schemas.user import (
    ResetPasswordRequest,
    UserCreate,
    UserOut,
    UserUpdate,
    VALID_ROLES,
)

__all__ = [
    "LoginRequest",
    "LoginResponse",
    "UserBrief",
    "ChangePasswordRequest",
    "PageResult",
    "PasswordStrengthError",
    "page_meta",
    "validate_password_strength",
    "DepartmentCreate",
    "DepartmentUpdate",
    "DepartmentOut",
    "CategoryCreate",
    "CategoryUpdate",
    "TagCreate",
    "TagUpdate",
    "DocumentCreate",
    "DocumentUpdate",
    "ArchiveRequest",
    "RestoreRequest",
    "UserCreate",
    "UserUpdate",
    "UserOut",
    "ResetPasswordRequest",
    "VALID_ROLES",
]