"""操作日志模型（docs/DATABASE.md §13、DESIGN.md §9、项目规范 §25）。"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.user import User


class AuditLog(Base):
    """操作日志：关键操作留痕，与业务操作同事务写入。"""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_object", "object_type", "object_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )  # NULL=系统自动（如自动失效写入）
    username: Mapped[Optional[str]] = mapped_column(
        String(64)
    )  # 操作人快照（防删除后失联）
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    object_type: Mapped[Optional[str]] = mapped_column(String(32))
    object_id: Mapped[Optional[int]] = mapped_column(Integer)
    object_label: Mapped[Optional[str]] = mapped_column(String(255))
    detail: Mapped[Optional[str]] = mapped_column(Text)  # JSON 字符串
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )

    user: Mapped[Optional["User"]] = relationship(
        "User", back_populates="audit_logs"
    )

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} action={self.action!r} at={self.created_at!r}>"