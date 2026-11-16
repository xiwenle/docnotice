"""ORM 模型统一入口。

导入本模块即注册全部表到 Base.metadata（建表 / Alembic 均依赖此注册）。
"""
from app.models.audit import AuditLog
from app.models.backup import BackupRecord, RecycleBin
from app.models.category import Category, Tag, document_tags
from app.models.document import (
    AUDIENCE_SCOPES,
    Document,
    DocumentTarget,
    FileRecord,
)
from app.models.notice import (
    DEFAULT_NOTICE_TYPES,
    NOTICE_STATUSES,
    Notice,
    NoticeRead,
    NoticeTarget,
    NoticeType,
    TARGET_TYPES,
    notice_documents,
)
from app.models.user import Department, User, user_departments

__all__ = [
    "User",
    "Department",
    "user_departments",
    "Category",
    "Tag",
    "document_tags",
    "Document",
    "DocumentTarget",
    "AUDIENCE_SCOPES",
    "FileRecord",
    "Notice",
    "notice_documents",
    "NoticeTarget",
    "NoticeRead",
    "NoticeType",
    "AuditLog",
    "BackupRecord",
    "RecycleBin",
    "DEFAULT_NOTICE_TYPES",
    "NOTICE_STATUSES",
    "TARGET_TYPES",
]