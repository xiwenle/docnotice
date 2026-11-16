"""通知及其关联模型（docs/DATABASE.md §9–§12，§17–§22）。

- notices：通知主体（状态机 published → cancelled）

  2026-11-04 产品决策：**移除 draft（草稿）状态**。
  以前新建通知先落成草稿，再调 /publish 二次发布；界面上却只有
  「创建并发布」一个按钮，草稿只在发布失败时残留，成了没人认领的半成品。
  现在创建即发布（同一事务内建库 + 物化快照 + 废止/替换联动），
  状态只剩 published / cancelled。
- notice_documents：通知-资料 M:N（§19）
- notice_targets：阅读对象规则（all / department / user，§21）
- notice_reads：发布时物化的阅读/确认快照（§20、§21）
- notice_types：可自定义的通知类型词表（替换原固定六类的 DB CHECK）
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.document import Document, FileRecord
    from app.models.user import Department, User

# 默认种子类型（首次建表时写入 notice_types；之后管理员可增删改/自定义）
DEFAULT_NOTICE_TYPES = ("发布", "修订", "替换", "废止", "延期", "其他")
NOTICE_STATUSES = ("published", "cancelled")
TARGET_TYPES = ("all", "department", "user")


class NoticeType(Base):
    """通知类型（可自定义，替代原固定六类 CHECK 约束）。"""

    __tablename__ = "notice_types"
    __table_args__ = (
        UniqueConstraint("name", name="uq_notice_types_name"),
        Index("ix_notice_types_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )

    def __repr__(self) -> str:
        return f"<NoticeType id={self.id} name={self.name!r}>"


class Notice(Base):
    """通知（§17、§18、§22）。"""

    __tablename__ = "notices"
    __table_args__ = (
        CheckConstraint(
            "status IN ('published','cancelled')", name="ck_notices_status"
        ),
        Index("ix_notices_status", "status"),
        Index("ix_notices_type", "notice_type"),
        Index("ix_notices_published_at", "published_at"),
        Index("ix_notices_effective_at", "effective_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text)
    notice_type: Mapped[Optional[str]] = mapped_column(String(32))
    # 创建即发布（2026-11-04）：默认 published，published_at 由 service 写入
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="published", server_default="published"
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    effective_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    file_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("file_records.id"), nullable=True
    )
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    updated_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
    modified_reason: Mapped[Optional[str]] = mapped_column(String(255))
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(255))
    # L-1：废止/替换通知发布时存储受影响资料的旧 effective_to（JSON 字符串）
    # 取消时据此回滚
    auto_expired_docs: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )

    file: Mapped[Optional["FileRecord"]] = relationship("FileRecord")  # noqa: F821
    documents: Mapped[list["Document"]] = relationship(  # noqa: F821
        "Document", secondary="notice_documents", back_populates="linked_notices"
    )
    targets: Mapped[list["NoticeTarget"]] = relationship(
        "NoticeTarget", back_populates="notice", cascade="all, delete-orphan"
    )
    reads: Mapped[list["NoticeRead"]] = relationship(
        "NoticeRead", back_populates="notice", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Notice id={self.id} title={self.title!r} status={self.status!r}>"


# notice_documents：通知-资料 M:N（§10、§19）
notice_documents = Table(
    "notice_documents",
    Base.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("notice_id", Integer, ForeignKey("notices.id"), nullable=False),
    Column("document_id", Integer, ForeignKey("documents.id"), nullable=False),
    UniqueConstraint("notice_id", "document_id", name="uq_notice_documents"),
    Index("ix_notice_documents_document_id", "document_id"),
)


class NoticeTarget(Base):
    """阅读对象规则（§11、§21）：all / department / user。"""

    __tablename__ = "notice_targets"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('all','department','user')", name="ck_notice_targets_type"
        ),
        UniqueConstraint(
            "notice_id", "target_type", "department_id", "user_id",
            name="uq_notice_targets",
        ),
        Index("ix_notice_targets_notice_id", "notice_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    notice_id: Mapped[int] = mapped_column(
        ForeignKey("notices.id"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    department_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("departments.id"), nullable=True
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )

    notice: Mapped[Notice] = relationship("Notice", back_populates="targets")
    department: Mapped[Optional["Department"]] = relationship("Department")  # noqa: F821
    user: Mapped[Optional["User"]] = relationship("User")  # noqa: F821


class NoticeRead(Base):
    """阅读快照（§12、§20、§21）：发布时物化，每名应阅读用户一行。

    2026-11-03 产品决策：移除「确认」功能（`confirmed_at` / `confirm_ip`），
    只保留「已查看/未查看」。查看与确认不再区分，统计口径简化为单一维度。
    """

    __tablename__ = "notice_reads"
    __table_args__ = (
        UniqueConstraint("notice_id", "user_id", name="uq_notice_reads"),
        Index("ix_notice_reads_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    notice_id: Mapped[int] = mapped_column(
        ForeignKey("notices.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    read_ip: Mapped[Optional[str]] = mapped_column(String(64))

    notice: Mapped[Notice] = relationship("Notice", back_populates="reads")
    user: Mapped["User"] = relationship("User", back_populates="notice_reads")  # noqa: F821