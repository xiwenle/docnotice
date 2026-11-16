"""资料 Schema（docs/API.md §6）。"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    """POST /api/v1/documents 请求体。"""

    title: str = Field(min_length=1, max_length=255)
    category_id: Optional[int] = None
    summary: Optional[str] = Field(default=None, max_length=10000)
    effective_from: date
    effective_to: Optional[date] = None
    tag_ids: list[int] = []
    # 可见范围（2026-11-03）：all 全体 / department 指定部门 / user 指定人员
    audience_scope: Literal["all", "department", "user"] = "all"
    audience_department_ids: list[int] = []
    audience_user_ids: list[int] = []


class DocumentUpdate(BaseModel):
    """PUT /api/v1/documents/{id} 请求体（可部分）。

    - 时效改动触 R2：重叠需 confirm_overwrite=true + modify_reason（§16）
    - effective_to 传 null 表示清除失效日期（暂无失效日期，§13）
    """

    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    category_id: Optional[int] = None
    summary: Optional[str] = Field(default=None, max_length=10000)
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    tag_ids: Optional[list[int]] = None
    audience_scope: Optional[Literal["all", "department", "user"]] = None
    audience_department_ids: Optional[list[int]] = None
    audience_user_ids: Optional[list[int]] = None
    confirm_overwrite: bool = False
    modify_reason: Optional[str] = Field(default=None, max_length=255)


class DocumentBulkUpdate(BaseModel):
    """POST /api/v1/documents/bulk 请求体（批量整理：仅管理员）。

    - set_category_id：批量设置分类（须存在且未停用）；与 clear_category 二选一
    - clear_category：批量清除分类（置为未分类）
    - add_tag_id：批量追加标签（幂等，已有的不重复加）
    - archive：批量删除（归档进回收站，复用单条归档语义）
    - 至少提供一项操作
    """

    document_ids: list[int] = Field(min_length=1, max_length=200)
    set_category_id: Optional[int] = None
    clear_category: bool = False
    add_tag_id: Optional[int] = None
    archive: bool = False
    reason: Optional[str] = Field(default=None, max_length=255)


class DocumentBatchSelection(BaseModel):
    """批量动作的选择范围。

    mode=ids：显式选择资料 id；
    mode=filter：按当前筛选条件由服务端解析，exclude_ids 可排除少量条目。
    """

    mode: Literal["ids", "filter"]
    ids: list[int] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    exclude_ids: list[int] = Field(default_factory=list)


class DocumentBatchPreviewRequest(BaseModel):
    selection: DocumentBatchSelection
    params: dict[str, Any] = Field(default_factory=dict)
    expected_count: Optional[int] = Field(default=None, ge=0)
    reason: Optional[str] = Field(default=None, max_length=255)


class DocumentBatchExecuteRequest(DocumentBatchPreviewRequest):
    preview_token: str = Field(min_length=8, max_length=200)
class ArchiveRequest(BaseModel):
    """DELETE /api/v1/documents/{id} 可选请求体。"""

    reason: Optional[str] = Field(default=None, max_length=255)


class RestoreRequest(BaseModel):
    """POST /api/v1/documents/{id}/restore 可选请求体。"""

    reason: Optional[str] = Field(default=None, max_length=255)