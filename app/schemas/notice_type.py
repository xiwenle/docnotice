"""通知类型 Schema（自定义类型词表）。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class NoticeTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    sort_order: int = 0


class NoticeTypeUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=32)
    sort_order: Optional[int] = None