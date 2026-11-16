"""业务异常与统一错误契约（docs/DESIGN.md §8、docs/API.md §0.2）。

失败响应统一为：
    {"error": {"code": "<CODE>", "message": "<中文可读信息>", "detail": {...}}}
"""
from __future__ import annotations

from typing import Any, Optional

# 错误码 → HTTP 状态码（DESIGN §8.2 全集）
ERROR_HTTP_STATUS = {
    "UNAUTHORIZED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "VALIDATION_ERROR": 400,
    "VALIDITY_CONFLICT": 409,
    "NOTICE_MODIFY_REQUIRES_REASON": 409,
    "FILE_TYPE_NOT_ALLOWED": 415,
    "FILE_TYPE_MISMATCH": 415,
    "FILE_TOO_LARGE": 413,
    "DUPLICATE": 409,
    "IN_USE": 409,
    "CONFLICT": 409,
    "BAD_REQUEST": 400,
    "PASSWORD_CHANGE_REQUIRED": 403,
    "INTERNAL_ERROR": 500,
    "RATE_LIMITED": 429,
}


class AppError(Exception):
    """业务异常：携带稳定错误码，由全局异常处理器转换为统一错误响应。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        detail: Optional[dict[str, Any]] = None,
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.status_code = status_code or ERROR_HTTP_STATUS.get(code, 500)


def error_payload(
    code: str, message: str, detail: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """构建统一错误响应体。"""
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if detail is not None:
        payload["error"]["detail"] = detail
    return payload