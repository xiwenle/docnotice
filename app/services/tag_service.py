"""标签服务（docs/API.md §5、DATABASE.md §5、项目规范 §9）。

- 扁平词表，与分类无关；名称全局唯一
- 有资料引用时禁止删除（IN_USE）
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.category import Tag, document_tags
from app.models.document import Document
from app.services import audit_service
from app.core.cache import invalidate_lists


def _invalidate_caches() -> None:
    """标签变更影响资料列表与工作台计数，写后即失效（统一口径）。"""
    invalidate_lists()


def list_tags(db: Session, *, keyword: Optional[str] = None, with_count: bool = False) -> dict:
    """标签列表：{"items": [{id, name, document_count?}]}。"""
    stmt = select(Tag).order_by(Tag.id)
    if keyword:
        stmt = stmt.where(Tag.name.like(f"%{keyword}%"))
    tags = db.execute(stmt).scalars().all()

    counts: dict[int, int] = {}
    if with_count and tags:
        rows = db.execute(
            select(document_tags.c.tag_id, func.count(document_tags.c.document_id))
            .join(Document, Document.id == document_tags.c.document_id)
            .where(
                document_tags.c.tag_id.in_([t.id for t in tags]),
                Document.archived_at.is_(None),  # 与资料列表按标签筛选同口径（§26 软删不计）
            )
            .group_by(document_tags.c.tag_id)
        ).all()
        counts = {tid: cnt for tid, cnt in rows}

    items = []
    for t in tags:
        item = {"id": t.id, "name": t.name}
        if with_count:
            item["document_count"] = counts.get(t.id, 0)
        items.append(item)
    return {"items": items}


def create_tag(db: Session, *, current_user, name: str, ip=None, user_agent=None) -> Tag:
    name = name.strip()
    if not name:
        raise AppError("VALIDATION_ERROR", "标签名不能为空")
    if db.execute(select(Tag.id).where(Tag.name == name)).scalar_one_or_none() is not None:
        raise AppError("DUPLICATE", f"标签已存在: {name}")

    tag = Tag(name=name)
    db.add(tag)
    db.flush()
    audit_service.log(
        db,
        action=audit_service.ACTION_CREATE_TAG,
        user=current_user,
        object_type="tag",
        object_id=tag.id,
        object_label=tag.name,
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    _invalidate_caches()
    db.refresh(tag)
    return tag


def update_tag(db: Session, *, current_user, tag_id: int, name: str, ip=None, user_agent=None) -> Tag:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise AppError("NOT_FOUND", "标签不存在")
    name = name.strip()
    if not name:
        raise AppError("VALIDATION_ERROR", "标签名不能为空")
    if name != tag.name:
        dup = db.execute(
            select(Tag.id).where(Tag.name == name, Tag.id != tag_id)
        ).scalar_one_or_none()
        if dup is not None:
            raise AppError("DUPLICATE", f"标签已存在: {name}")
        tag.name = name

    audit_service.log(
        db,
        action=audit_service.ACTION_UPDATE_TAG,
        user=current_user,
        object_type="tag",
        object_id=tag.id,
        object_label=tag.name,
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    _invalidate_caches()
    db.refresh(tag)
    return tag


def delete_tag(db: Session, *, current_user, tag_id: int, ip=None, user_agent=None) -> dict:
    """删除标签（2026-11-09 用户确认）：只删标签和资料关联，不删资料。

    资料上的该标签会被移除，进入「未打标签」状态；资料分类、
    文件、附件、通知均不受影响。
    """
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise AppError("NOT_FOUND", "标签不存在")
    ref_count = db.execute(
        select(func.count(document_tags.c.id)).where(document_tags.c.tag_id == tag_id)
    ).scalar_one()
    if ref_count > 0:
        db.execute(document_tags.delete().where(document_tags.c.tag_id == tag_id))

    name = tag.name
    db.delete(tag)
    audit_service.log(
        db,
        action=audit_service.ACTION_DELETE_TAG,
        user=current_user,
        object_type="tag",
        object_id=tag_id,
        object_label=name,
        detail={"affected_document_count": ref_count},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    _invalidate_caches()
    return {
        "ok": True,
        "id": tag_id,
        "name": name,
        "affected_document_count": ref_count,
    }
