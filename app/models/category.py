"""分类 / 标签 / 资料-标签关联模型（docs/DATABASE.md §4–§5、§8）。

- 分类：一级~四级树（categories.parent_id 自引用，深度 ≤4，service 层校验，§9）
- 标签：扁平词表，与分类无关
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Category(Base):
    """分类（§4：一级 ~ 四级树）。"""

    __tablename__ = "categories"
    __table_args__ = (
        # 同级下名称唯一，但只对未删除（is_active=1）的行生效：
        # 已删除分类不可见，不应占用名字（部分唯一索引；旧全行约束由
        # scripts/migrate_partial_unique.py 迁移）。
        # SQLite 中 NULL 视为互不相同，一级分类唯一性由 service 层校验兜底。
        Index(
            "uq_categories_parent_name_active",
            "parent_id", "name",
            unique=True,
            sqlite_where=text("is_active = 1"),
        ),
        Index("ix_categories_parent_id", "parent_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("categories.id"), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )

    parent: Mapped[Optional["Category"]] = relationship(
        "Category", remote_side=[id], back_populates="children"
    )
    children: Mapped[list["Category"]] = relationship(
        "Category", back_populates="parent"
    )

    def __repr__(self) -> str:
        return f"<Category id={self.id} name={self.name!r}>"


class Tag(Base):
    """标签（§5：扁平词表）。"""

    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("name", name="uq_tags_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )

    def __repr__(self) -> str:
        return f"<Tag id={self.id} name={self.name!r}>"


# document_tags：资料-标签 M:N（§8）
document_tags = Table(
    "document_tags",
    Base.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("document_id", Integer, ForeignKey("documents.id"), nullable=False),
    Column("tag_id", Integer, ForeignKey("tags.id"), nullable=False),
    UniqueConstraint("document_id", "tag_id", name="uq_document_tags"),
    Index("ix_document_tags_tag_id", "tag_id"),
)