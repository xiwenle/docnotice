"""通知类型服务（自定义类型词表，替代原固定六类）。

- 名称全局唯一；删除时若被通知引用 → IN_USE 阻止（保守，与分类/标签一致）
- 默认种子六类由 init_db 写入；管理员可增删改
"""
from __future__ import annotations

from typing import Optional, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.notice import Notice, NoticeType
from app.services import audit_service
from app.services.notice_linkage import AUTO_EXPIRE_TYPES


def list_notice_types(db: Session, *, keyword: Optional[str] = None, with_count: bool = False) -> dict:
    """通知类型列表：{"items": [{id, name, sort_order, is_active, notice_count?}]}。"""
    stmt = select(NoticeType).order_by(NoticeType.sort_order, NoticeType.id)
    if keyword:
        stmt = stmt.where(NoticeType.name.like(f"%{keyword}%"))
    types = db.execute(stmt).scalars().all()

    counts: dict[int, int] = {}
    if with_count and types:
        rows = db.execute(
            select(Notice.notice_type, func.count(Notice.id))
            .where(Notice.notice_type.in_([t.name for t in types]))
            .group_by(Notice.notice_type)
        ).all()
        counts = {t.id: cnt for t in types for name, cnt in rows if name == t.name}

    items = []
    for t in types:
        item = {
            "id": t.id,
            "name": t.name,
            "sort_order": t.sort_order,
            "is_active": bool(t.is_active),
        }
        if with_count:
            item["notice_count"] = counts.get(t.id, 0)
        items.append(item)
    return {"items": items}


def create_notice_type(
    db: Session, *, current_user, name: str, sort_order: int = 0, ip=None, user_agent=None
) -> NoticeType:
    name = name.strip()
    if not name:
        raise AppError("VALIDATION_ERROR", "通知类型名不能为空")
    if len(name) > 32:
        raise AppError("VALIDATION_ERROR", "通知类型名不能超过 32 个字符")
    if db.execute(select(NoticeType.id).where(NoticeType.name == name)).scalar_one_or_none() is not None:
        raise AppError("DUPLICATE", f"通知类型已存在: {name}")

    nt = NoticeType(name=name, sort_order=sort_order or 0, is_active=True)
    db.add(nt)
    db.flush()
    audit_service.log(
        db,
        action=audit_service.ACTION_CREATE_NOTICE_TYPE,
        user=current_user,
        object_type="notice_type",
        object_id=nt.id,
        object_label=nt.name,
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    db.refresh(nt)
    return nt


def update_notice_type(
    db: Session, *, current_user, notice_type_id: int, name: Optional[str] = None,
    sort_order: Optional[int] = None,
    ip=None, user_agent=None,
) -> NoticeType:
    nt = db.get(NoticeType, notice_type_id)
    if nt is None:
        raise AppError("NOT_FOUND", "通知类型不存在")

    renamed_from = None
    affected_notices = 0
    if name is not None:
        name = name.strip()
        if not name:
            raise AppError("VALIDATION_ERROR", "通知类型名不能为空")
        if len(name) > 32:
            raise AppError("VALIDATION_ERROR", "通知类型名不能超过 32 个字符")
        if name != nt.name:
            if nt.name in AUTO_EXPIRE_TYPES:
                raise AppError(
                    "CONFLICT",
                    f"「{nt.name}」按名称驱动资料时效联动，改名会静默断开联动，禁止改名",
                )
            dup = db.execute(
                select(NoticeType.id).where(
                    NoticeType.name == name, NoticeType.id != notice_type_id
                )
            ).scalar_one_or_none()
            if dup is not None:
                raise AppError("DUPLICATE", f"通知类型已存在: {name}")
            # 通知表存的是类型名字符串：改名必须级联，否则老通知再保存会被
            # _validate_notice_type 判「类型不存在」，且引用计数按新名算=0 可被误删
            renamed_from = nt.name
            nt.name = name
            result = cast(
                CursorResult,
                db.execute(
                    update(Notice)
                    .where(Notice.notice_type == renamed_from)
                    .values(notice_type=name)
                ),
            )
            affected_notices = result.rowcount
    if sort_order is not None:
        nt.sort_order = sort_order

    audit_service.log(
        db,
        action=audit_service.ACTION_UPDATE_NOTICE_TYPE,
        user=current_user,
        object_type="notice_type",
        object_id=nt.id,
        object_label=nt.name,
        detail=(
            {"rename_from": renamed_from, "cascaded_notices": affected_notices}
            if renamed_from
            else None
        ),
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    db.refresh(nt)
    return nt


def delete_notice_type(db: Session, *, current_user, notice_type_id: int, ip=None, user_agent=None) -> dict:
    """删除通知类型（2026-11-09 用户确认）。

    - 普通类型：已有通知的 notice_type 置空为「未分类」，通知本身不删；
    - 「废止 / 替换」是资料时效联动类型，仍禁止删除，避免静默断开联动。
    """
    nt = db.get(NoticeType, notice_type_id)
    if nt is None:
        raise AppError("NOT_FOUND", "通知类型不存在")
    if nt.name in AUTO_EXPIRE_TYPES:
        raise AppError(
            "CONFLICT",
            f"通知类型「{nt.name}」是资料时效联动类型，不能删除（会破坏废止/替换联动）",
        )
    ref_count = db.execute(
        select(func.count(Notice.id)).where(Notice.notice_type == nt.name)
    ).scalar_one()
    name = nt.name
    if ref_count > 0:
        db.execute(
            update(Notice)
            .where(Notice.notice_type == name)
            .values(notice_type=None)
        )

    db.delete(nt)
    audit_service.log(
        db,
        action=audit_service.ACTION_DELETE_NOTICE_TYPE,
        user=current_user,
        object_type="notice_type",
        object_id=notice_type_id,
        object_label=name,
        detail={"affected_notice_count": ref_count},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return {
        "ok": True,
        "id": notice_type_id,
        "name": name,
        "affected_notice_count": ref_count,
    }
