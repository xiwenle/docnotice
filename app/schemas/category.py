"""分类 Schema（docs/API.md §4）。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    parent_id: Optional[int] = None
    sort_order: int = 0


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    parent_id: Optional[int] = None
    sort_order: Optional[int] = None
