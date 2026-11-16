"""标签 Schema（docs/API.md §5）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class TagUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=64)