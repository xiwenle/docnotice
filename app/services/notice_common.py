"""通知服务共享底层：加载器 / 缓存失效 / 可见性 / 入参校验。

2026-10-26 从 notice_service.py（1555 行）按域拆分而来：代码逐字搬移，
仅把跨模块使用的私有符号改为公开名（`_load_notice` → `load_notice` 等）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError
from app.models.document import Document
from app.models.notice import (
    Notice,
    NoticeRead,
    NoticeTarget as NoticeTargetModel,
    NoticeType,
)
from app.models.user import Department, User, user_departments
from app.schemas.notice import NoticeTarget


def load_notice(db: Session, notice_id: int) -> Notice:
    notice = db.execute(
        select(Notice)
        .options(
            selectinload(Notice.documents),
            selectinload(Notice.targets),
            selectinload(Notice.file),
        )
        .where(Notice.id == notice_id)
    ).scalar_one_or_none()
    if notice is None:
        raise AppError("NOT_FOUND", "通知不存在")
    return notice



def my_snapshot(db: Session, notice_id: int, user_id: int) -> Optional[NoticeRead]:
    return db.execute(
        select(NoticeRead).where(
            NoticeRead.notice_id == notice_id, NoticeRead.user_id == user_id
        )
    ).scalar_one_or_none()



def invalidate_caches(*, lists: bool = False) -> None:
    """统一缓存失效（S3）：dashboard 必清；lists=True 时同时清资料列表。"""
    from app.core import cache as cache_layer
    if lists:
        cache_layer.invalidate_lists()  # 已含 dashboard
    else:
        cache_layer.invalidate_dashboard()



def is_visible_to_user(notice: Notice, now: Optional[datetime] = None) -> bool:
    """普通用户可见性：published 且（effective_at 空或 <= now）。"""
    now = now or datetime.now()
    if notice.status != "published":
        return False
    if notice.effective_at is not None and notice.effective_at > now:
        return False
    return True



def validate_notice_type(db: Session, notice_type: str) -> None:
    """通知类型必须存在于自定义词表 notice_types 且未停用（含历史六类种子）。"""
    row = db.execute(
        select(NoticeType.id, NoticeType.is_active).where(NoticeType.name == notice_type)
    ).first()
    if row is None:
        raise AppError(
            "VALIDATION_ERROR",
            f"通知类型「{notice_type}」不存在，请先在「通知类型」管理/新建通知时内联创建",
        )
    if not row.is_active:
        raise AppError(
            "VALIDATION_ERROR",
            f"通知类型「{notice_type}」已停用，请先在「通知类型」管理中启用后再使用",
        )



def load_documents(db: Session, document_ids: list[int]) -> list[Document]:
    if not document_ids:
        return []
    docs = db.execute(
        select(Document).where(Document.id.in_(document_ids))
    ).scalars().all()
    found = {d.id for d in docs}
    missing = [i for i in document_ids if i not in found]
    if missing:
        raise AppError("VALIDATION_ERROR", f"关联资料不存在: {missing}")
    # 已删除（回收站中）的资料不能关联通知——避免通知详情/联动失效指向不可见资料
    archived = [d.id for d in docs if d.archived_at is not None]
    if archived:
        raise AppError(
            "VALIDATION_ERROR",
            f"关联资料已删除（回收站中），不能关联: {archived}；"
            "如需关联请先在回收站恢复该资料",
        )
    return list(docs)



def validate_target_ids(db: Session, target: NoticeTarget) -> None:
    """阅读对象引用校验：scope=user/department 时 id 必须真实存在（防 FK 500）。"""
    if target.scope == "user":
        existing = set(db.execute(select(User.id).where(User.id.in_(target.user_ids))).scalars())
        missing = [i for i in target.user_ids if i not in existing]
        if missing:
            raise AppError("VALIDATION_ERROR", f"阅读对象用户不存在: {missing}")
    elif target.scope == "department":
        existing = set(db.execute(
            select(Department.id).where(Department.id.in_(target.department_ids))
        ).scalars())
        missing = [i for i in target.department_ids if i not in existing]
        if missing:
            raise AppError("VALIDATION_ERROR", f"阅读对象部门不存在: {missing}")



def target_to_rows(target: NoticeTarget) -> list[NoticeTargetModel]:
    """target 规则 → notice_targets 行（'all' 每通知仅一行；department/user 每 id 一行）。"""
    rows: list[NoticeTargetModel] = []
    if target.scope == "all":
        rows.append(NoticeTargetModel(target_type="all"))
    elif target.scope == "department":
        for dept_id in target.department_ids:
            rows.append(
                NoticeTargetModel(target_type="department", department_id=dept_id)
            )
    else:
        for uid in target.user_ids:
            rows.append(NoticeTargetModel(target_type="user", user_id=uid))
    return rows



def audience_target(notice: Notice) -> NoticeTarget:
    """从 notice_targets 行还原 target 规则（service 内部用）。"""
    if not notice.targets:
        return NoticeTarget(scope="all")
    first = notice.targets[0]
    if first.target_type == "all":
        return NoticeTarget(scope="all")
    if first.target_type == "department":
        dept_ids = [t.department_id for t in notice.targets if t.department_id]
        return NoticeTarget(scope="department", department_ids=dept_ids)
    user_ids = [t.user_id for t in notice.targets if t.user_id]
    return NoticeTarget(scope="user", user_ids=user_ids)



def user_departments_map(db: Session, user_ids: list[int]) -> dict[int, list[str]]:
    if not user_ids:
        return {}
    from app.models.user import Department

    rows = db.execute(
        select(user_departments.c.user_id, Department.name)
        .join(Department, Department.id == user_departments.c.department_id)
        .where(user_departments.c.user_id.in_(user_ids))
    ).all()
    result: dict[int, list[str]] = {}
    for uid, name in rows:
        result.setdefault(uid, []).append(name)
    return result
