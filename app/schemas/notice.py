"""通知 Schema（docs/API.md §7）。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class NoticeTarget(BaseModel):
    """阅读对象规则：all / department / user（§21）。"""

    scope: Literal["all", "department", "user"]
    department_ids: list[int] = []
    user_ids: list[int] = []

    @model_validator(mode="after")
    def _check_ids(self):
        if self.scope == "department" and not self.department_ids:
            raise ValueError("scope=department 时 department_ids 不能为空")
        if self.scope == "user" and not self.user_ids:
            raise ValueError("scope=user 时 user_ids 不能为空")
        return self


class NoticeCreate(BaseModel):
    """POST /api/v1/notices 请求体（JSON 方式；也支持 multipart，见路由）。"""

    title: str = Field(min_length=1, max_length=255)
    content: Optional[str] = None
    # 2026-11-03 产品决策：通知类型非必填，默认空；来自可自定义类型词表
    notice_type: Optional[str] = Field(default=None, max_length=32)
    effective_at: Optional[datetime] = None  # 发布时间（生效时间），可空=发布即生效
    deadline_at: Optional[datetime] = None  # 截止时间（可选）：过期后不再计入待读
    document_ids: list[int] = []
    target: NoticeTarget = Field(default_factory=lambda: NoticeTarget(scope="all"))

    @model_validator(mode="after")
    def _require_content_or_file_later(self):
        # 标题必须有；正文可为空（纯文件通知，文件在 multipart 时提供）
        if not self.title.strip():
            raise ValueError("标题不能为空")
        return self


class NoticeUpdate(BaseModel):
    """PUT /api/v1/notices/{id} 请求体（JSON 方式）。"""

    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    content: Optional[str] = None
    notice_type: Optional[str] = Field(default=None, max_length=32)
    effective_at: Optional[datetime] = None
    deadline_at: Optional[datetime] = None
    document_ids: Optional[list[int]] = None
    target: Optional[NoticeTarget] = None
    confirm_modify: bool = False
    modified_reason: Optional[str] = Field(default=None, max_length=255)


class CancelRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=255)