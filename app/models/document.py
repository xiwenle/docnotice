"""资料 / 文件记录模型（docs/DATABASE.md §6–§7、§8）。

- 一个资料当前只有一个当前文件（documents.current_file_id，§8）
- 数据库不存二进制，文件落盘于 data/（§27）；file_records 只存元数据
- 没有 status 列：时效状态由 validity_service 动态计算（§14、§15），归档用 archived_at 标记
"""
from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.category import Category, Tag
    from app.models.notice import Notice
    from app.models.user import User

# 资料可见范围（2026-11-03 产品决策：新建资料可指定哪些人能看）
# all = 全体；department = 指定部门；user = 指定用户。
# 与通知的阅读对象（notice_targets）结构同构，但语义不同：
# 通知是「要求阅读」，资料是「允许查看」。
AUDIENCE_SCOPES = ("all", "department", "user")


class FileRecord(Base):
    """存储文件元数据（§6：资料/通知文件共用，owner_type 区分；数据库不存二进制）。"""

    __tablename__ = "file_records"
    __table_args__ = (
        CheckConstraint(
            "owner_type IN ('document','notice')", name="ck_file_records_owner"
        ),
        # 2026-11-03：图片成为资料主文件，库级 CHECK 必须同步扩容——
        # 只改 Python 层白名单而漏改这里，INSERT 会直接 500。
        # 2026-11-06：pptx 加入（仅允许作附件，作主文件由 MAIN_FILE_EXTENSIONS 拦）。
        CheckConstraint(
            "file_type IN ('pdf','docx','xlsx','xls','pptx',"
            "'jpg','jpeg','png','gif','webp')",
            name="ck_file_records_type",
        ),
        CheckConstraint(
            "status IN ('active','recycled')", name="ck_file_records_status"
        ),
        UniqueConstraint("internal_name", name="uq_file_records_internal_name"),
        Index("ix_file_records_owner", "owner_type", "owner_id"),
        Index("ix_file_records_sha256", "sha256"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_type: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    internal_name: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # uuid4().hex + ext，服务器唯一标识
    file_type: Mapped[str] = mapped_column(String(8), nullable=False)
    extension: Mapped[str] = mapped_column(String(8), nullable=False)  # 如 ".pdf"
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_rel_path: Mapped[str] = mapped_column(
        String(255), nullable=False
    )  # 相对 DATA_DIR：documents/<internal>
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )  # active=在用；recycled=在回收站
    uploaded_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )

    uploader: Mapped[Optional["User"]] = relationship(  # noqa: F821
        "User", foreign_keys=[uploaded_by]
    )

    def __repr__(self) -> str:
        return (
            f"<FileRecord id={self.id} name={self.original_filename!r} "
            f"type={self.file_type!r} status={self.status!r}>"
        )


class Document(Base):
    """资料（§7、§13、§14、§15、§16）。"""

    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_category_id", "category_id"),
        Index("ix_documents_effective_from", "effective_from"),
        Index("ix_documents_effective_to", "effective_to"),
        Index("ix_documents_archived", "archived_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    category_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("categories.id"), nullable=True
    )
    effective_from: Mapped[date_type] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[date_type]] = mapped_column(Date, nullable=True)
    effective_note: Mapped[Optional[str]] = mapped_column(String(255))
    # 可见范围（2026-11-03）：all / department / user。
    # 存量资料一律为 all（迁移 009 回填），保证升级后普通用户不会突然
    # 看不到任何东西。具体目标行见 document_targets。
    audience_scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="all", server_default="all"
    )
    # 2026-11-04 移除 allow_download（「仅预览」开关）。
    # 下载权限 = 查看权限：能打开这份资料的人就能下载默认文件；原来靠
    # audience_scope 精确到人已经够用，再叠一层开关只会造成两个入口语义打架。
    # 数据库里保留该列（历史数据不销毁），但代码不再读写。
    current_file_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("file_records.id"), nullable=True
    )
    # 附件（2026-11-06）：每个资料最多 1 个，永不预览，只供下载。
    # 与「主文件」是两条独立的线——附件不承担「必须与主文件对应」的语义，
    # 可以是 Word/PPT 原稿，也可以是完全无关的补充材料。
    # 存成第二条 FileRecord（owner_type='document', owner_id=本资料 id）；
    # FileRecord 本就是 owner 一对多，无需新表。
    attachment_file_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("file_records.id"), nullable=True
    )
    rev_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    archived_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    archive_reason: Mapped[Optional[str]] = mapped_column(String(255))
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    updated_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )

    category: Mapped[Optional["Category"]] = relationship("Category")  # noqa: F821
    # 当前文件（§8：单当前文件；可为空=暂无文件）
    current_file: Mapped[Optional["FileRecord"]] = relationship(
        "FileRecord", foreign_keys=[current_file_id], post_update=True
    )
    # 附件（2026-11-06）：与 current_file 并列的第二个文件槽。
    # lazy="selectin" 会拖慢列表页，故保持默认惰性加载——
    # 列表页只用 attachment_file_id 判空，详情页才真正取这条记录。
    attachment_file: Mapped[Optional["FileRecord"]] = relationship(
        "FileRecord", foreign_keys=[attachment_file_id], post_update=True
    )
    tags: Mapped[list["Tag"]] = relationship(  # noqa: F821
        "Tag", secondary="document_tags"
    )
    linked_notices: Mapped[list["Notice"]] = relationship(  # noqa: F821
        "Notice", secondary="notice_documents", back_populates="documents"
    )
    targets: Mapped[list["DocumentTarget"]] = relationship(
        "DocumentTarget", back_populates="document", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} title={self.title!r}>"


class DocumentTarget(Base):
    """资料可见范围的具体对象（2026-11-03）。

    与 NoticeTarget 同构：
    - scope=all：不写目标行（documents.audience_scope 已表达）
    - scope=department：每行一个 department_id
    - scope=user：每行一个 user_id
    """

    __tablename__ = "document_targets"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('all','department','user')",
            name="ck_document_targets_type",
        ),
        UniqueConstraint(
            "document_id", "target_type", "department_id", "user_id",
            name="uq_document_targets",
        ),
        Index("ix_document_targets_document_id", "document_id"),
        Index("ix_document_targets_department", "department_id"),
        Index("ix_document_targets_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id"), nullable=False
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

    document: Mapped[Document] = relationship(
        "Document", back_populates="targets"
    )