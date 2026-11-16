"""备份记录 / 回收站模型（docs/DATABASE.md §14–§15、DESIGN.md §10、项目规范 §26/§28）。"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.document import Document, FileRecord


class BackupRecord(Base):
    """备份记录（§14、§28）。"""

    __tablename__ = "backup_records"
    __table_args__ = (
        CheckConstraint(
            "backup_type IN ('full','database')", name="ck_backup_records_type"
        ),
        CheckConstraint(
            "status IN ('completed','failed','restored','purged')",
            name="ck_backup_records_status",
        ),
        Index("ix_backup_records_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backup_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="full", server_default="full"
    )  # full=库+文件；database=仅库
    backup_path: Mapped[str] = mapped_column(
        String(255), nullable=False
    )  # 相对 DATA_DIR 的备份目录
    manifest_path: Mapped[Optional[str]] = mapped_column(String(255))
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    db_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    file_count: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="completed", server_default="completed"
    )
    source: Mapped[str] = mapped_column(
        String(8), nullable=False, default="manual", server_default="manual"
    )  # manual=管理员手动；auto=调度自动备份（迁移 003）
    note: Mapped[Optional[str]] = mapped_column(String(255))
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
    restored_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    restored_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )


class RecycleBin(Base):
    """回收站（§15、§8、§26）。"""

    __tablename__ = "recycle_bin"
    __table_args__ = (
        # 2026-11-06：attachment_deleted 独立于 deleted。复用 deleted 会让
        # 「只删附件」在恢复时走进 deleted 分支、连带取消资料归档——
        # 删除语义在本项目返工过五轮，必须显式区分。
        CheckConstraint(
            "reason IN ('replaced','archived','deleted','attachment_deleted')",
            name="ck_recycle_bin_reason",
        ),
        Index("ix_recycle_bin_document_id", "document_id"),
        Index("ix_recycle_bin_status", "deleted_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("documents.id"), nullable=True
    )
    file_record_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("file_records.id"), nullable=True
    )  # 可空：无文件资料被删除时没有对应文件记录，仍须在回收站留档
    # 附件（2026-11-06）：归档资料时主文件与附件**共用同一条记录**（此处同填），
    # 恢复必须两个一起；单独替换/删除附件时只有这个字段有值。
    attachment_file_record_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("file_records.id"), nullable=True
    )
    reason: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # replaced=文件被替换；archived=资料归档；deleted=资料删除（软）；
    # attachment_deleted=附件被单独删除（不影响资料状态）
    origin_rel_path: Mapped[Optional[str]] = mapped_column(String(255))
    deleted_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
    restored_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    restored_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    purged_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    purged_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    document: Mapped[Optional["Document"]] = relationship("Document")  # noqa: F821
    file_record: Mapped["FileRecord"] = relationship(  # noqa: F821
        "FileRecord", foreign_keys=[file_record_id]
    )
    attachment_file_record: Mapped[Optional["FileRecord"]] = relationship(  # noqa: F821
        "FileRecord", foreign_keys=[attachment_file_record_id]
    )