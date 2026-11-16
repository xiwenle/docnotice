"""用户 / 部门 / 用户-部门关联模型（docs/DATABASE.md §1–§3）。"""
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
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.audit import AuditLog
    from app.models.notice import NoticeRead

# ---------------------------------------------------------------------------
# user_departments：用户-部门 M:N（§3）
# ---------------------------------------------------------------------------
user_departments = Table(
    "user_departments",
    Base.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("department_id", Integer, ForeignKey("departments.id"), nullable=False),
    UniqueConstraint("user_id", "department_id", name="uq_user_departments"),
    Index("ix_user_departments_department_id", "department_id"),
)


class User(Base):
    """用户（§1、§23、§24）。"""

    __tablename__ = "users"

    # 角色常量（权限模型 v3；与 ck_users_role CHECK 约束对应）
    ROLE_USER = "user"
    ROLE_ADMIN = "admin"

    __table_args__ = (
        CheckConstraint("role IN ('user','admin')", name="ck_users_role"),
        UniqueConstraint("username", name="uq_users_username"),
        UniqueConstraint("auth_source", "external_uid", name="uq_users_auth_ext"),
        Index("ix_users_role", "role"),
        Index("ix_users_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(128))
    phone: Mapped[Optional[str]] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="user", server_default="user"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    # 账号来源标识（当前仅 local）与外部账号标识，预留给将来的其他认证方式
    auth_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="local", server_default="local"
    )
    external_uid: Mapped[Optional[str]] = mapped_column(String(128))
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # 单点登录（2026-09-03 产品决策）：每次登录/改密/重置自增，JWT 携带签发时的
    # ver，与列值不等即 401 → 旧设备全部下线
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )

    departments: Mapped[list["Department"]] = relationship(
        "Department", secondary="user_departments", back_populates="users"
    )
    notice_reads: Mapped[list["NoticeRead"]] = relationship(  # noqa: F821
        "NoticeRead", back_populates="user"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(  # noqa: F821
        "AuditLog", back_populates="user"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} role={self.role!r}>"


class Department(Base):
    """部门（§2、§21）。"""

    __tablename__ = "departments"
    __table_args__ = (
        # 名称唯一（编码已于 2026-11-03 按产品决策移除），只对未删除
        # （is_active=1）的行生效：
        # 已删除部门不可见，不应占用名字（部分唯一索引；旧全行约束由
        # scripts/migrate_partial_unique.py 迁移）。
        Index(
            "uq_departments_name_active",
            "name",
            unique=True,
            sqlite_where=text("is_active = 1"),
        ),
        Index("ix_departments_is_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )

    users: Mapped[list["User"]] = relationship(
        "User", secondary="user_departments", back_populates="departments"
    )

    def __repr__(self) -> str:
        return f"<Department id={self.id} name={self.name!r}>"