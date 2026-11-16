"""部门管理服务（docs/API.md §2、§21）。

- 列表：登录用户可读（用于选择）；管理操作仅 admin+
- 重名 → 409 DUPLICATE；有成员时禁止停用 → 409 IN_USE
- 停用为软删除（is_active=false，不物理删，§26）
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.session import total_count
from app.models.document import DocumentTarget
from app.models.notice import NoticeTarget
from app.models.user import Department, User, user_departments
from app.schemas.common import page_meta
from app.services import audit_service


def list_departments(
    db: Session,
    *,
    is_active: Optional[bool] = None,
    ids: Optional[list[int]] = None,
    keyword: Optional[str] = None,
    with_member_count: bool = False,
    page: int = 1,
    page_size: int = 100,
) -> dict:
    stmt = select(Department)
    if ids is not None:
        stmt = stmt.where(Department.id.in_(ids))
    if is_active is not None:
        stmt = stmt.where(Department.is_active.is_(is_active))
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(Department.name.like(like))

    total = total_count(db, stmt)

    deps = db.execute(
        stmt.order_by(Department.id).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()

    counts: dict[int, int] = {}
    if with_member_count and deps:
        # 成员数只统计启用用户（与通知统计分母 §21 口径一致，停用用户不计）
        rows = db.execute(
            select(
                user_departments.c.department_id,
                func.count(user_departments.c.user_id),
            )
            .join(User, User.id == user_departments.c.user_id)
            .where(
                user_departments.c.department_id.in_([d.id for d in deps]),
                User.is_active.is_(True),
            )
            .group_by(user_departments.c.department_id)
        ).all()
        counts = {dept_id: cnt for dept_id, cnt in rows}

    items = []
    for d in deps:
        item = {
            "id": d.id,
            "name": d.name,
            "description": d.description,
            "is_active": bool(d.is_active),
        }
        if with_member_count:
            item["member_count"] = counts.get(d.id, 0)
        items.append(item)

    return {"items": items, **page_meta(total, page, page_size)}


def create_department(
    db: Session,
    *,
    current_user,
    name: str,
    description: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Department:
    # 重名检查只看未删除的部门（已删除部门不可见、不占名）
    if db.execute(
        select(Department.id).where(
            Department.name == name, Department.is_active.is_(True)
        )
    ).scalar_one_or_none() is not None:
        raise AppError("DUPLICATE", f"部门名称已存在: {name}")

    dep = Department(name=name, description=description, is_active=True)
    db.add(dep)
    db.flush()
    audit_service.log(
        db,
        action=audit_service.ACTION_CREATE_DEPARTMENT,
        user=current_user,
        object_type="department",
        object_id=dep.id,
        object_label=dep.name,
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    db.refresh(dep)
    return dep


def update_department(
    db: Session,
    *,
    current_user,
    dep_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    is_active: Optional[bool] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Department:
    dep = db.get(Department, dep_id)
    if dep is None:
        raise AppError("NOT_FOUND", "部门不存在")

    if name is not None and name != dep.name:
        dup = db.execute(
            select(Department.id).where(
                Department.name == name, Department.id != dep_id,
                Department.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise AppError("DUPLICATE", f"部门名称已存在: {name}")
        dep.name = name
    if description is not None:
        dep.description = description
    if is_active is not None:
        dep.is_active = is_active

    audit_service.log(
        db,
        action=audit_service.ACTION_UPDATE_DEPARTMENT,
        user=current_user,
        object_type="department",
        object_id=dep.id,
        object_label=dep.name,
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    db.refresh(dep)
    return dep


def delete_department(
    db: Session,
    *,
    current_user,
    dep_id: int,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """删除部门（2026-11-09 用户确认）：不因有成员而拒绝。

    删除部门后，成员归属关联全部摘除；用户没有其他部门时显示「未分配部门」。
    通知的该部门阅读目标行摘除，通知本身、用户、资料、标签均不删除。
    删除后部门名即释放，可重建同名。
    """
    dep = db.get(Department, dep_id)
    if dep is None:
        raise AppError("NOT_FOUND", "部门不存在")
    member_count = db.execute(
        select(func.count(user_departments.c.id)).where(
            user_departments.c.department_id == dep_id
        )
    ).scalar_one()

    notice_target_count = db.execute(
        select(func.count(NoticeTarget.id)).where(NoticeTarget.department_id == dep_id)
    ).scalar_one()
    if notice_target_count:
        db.execute(delete(NoticeTarget).where(NoticeTarget.department_id == dep_id))

    # 资料可见范围也可能指定该部门；FK NO ACTION 必须先摘目标行。
    # 保守处理：不移除资料、不把资料改成全体可见，避免静默放大权限；
    # 没有其他部门目标时该资料只剩管理员可见，管理员可事后重新设置。
    document_target_count = db.execute(
        select(func.count(DocumentTarget.id)).where(DocumentTarget.department_id == dep_id)
    ).scalar_one()
    if document_target_count:
        db.execute(delete(DocumentTarget).where(DocumentTarget.department_id == dep_id))

    db.execute(
        user_departments.delete().where(user_departments.c.department_id == dep_id)
    )
    name = dep.name
    db.delete(dep)
    audit_service.log(
        db,
        action=audit_service.ACTION_DELETE_DEPARTMENT,
        user=current_user,
        object_type="department",
        object_id=dep_id,
        object_label=name,
        detail={
            "op": "delete",
            "affected_member_count": member_count,
            "notice_targets_removed": notice_target_count,
            "document_targets_removed": document_target_count,
        },
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    return {
        "ok": True,
        "id": dep_id,
        "name": name,
        "affected_member_count": member_count,
        "notice_targets_removed": notice_target_count,
        "document_targets_removed": document_target_count,
    }
