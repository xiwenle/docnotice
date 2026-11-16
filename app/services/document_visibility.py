"""资料可见范围（2026-11-03 产品决策）。

语义与通知的「阅读对象」不同，务必区分：

- 通知：target 决定**谁必须读**（物化快照、算未读、算统计）；
- 资料：audience 决定**谁可以看**（不产生任何"已读"记录）。

管理员始终可见全部资料（管理职责），不受 audience 限制——与通知一致。

数据模型：
- `documents.audience_scope` ∈ {all, department, user}
- `document_targets`：scope=department/user 时的具体对象行；
  scope=all 不写目标行。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.models.document import AUDIENCE_SCOPES, Document, DocumentTarget
from app.models.user import User, user_departments


def user_department_ids(db: Session, user_id: int) -> set[int]:
    """该用户所属部门 id 集合（与通知侧同名函数语义一致）。"""
    return set(
        db.execute(
            select(user_departments.c.department_id).where(
                user_departments.c.user_id == user_id
            )
        ).scalars().all()
    )


def visibility_condition(user: User):
    """返回一个 SQLAlchemy 条件：当前用户可见的资料。

    管理员 → 恒真（None 表示不加条件，调用方直接跳过）。
    普通用户 → audience_scope='all' OR 命中其部门 OR 命中其本人。
    """
    if user.role == User.ROLE_ADMIN:
        return None

    department_hit = exists().where(
        DocumentTarget.document_id == Document.id,
        DocumentTarget.target_type == "department",
        DocumentTarget.department_id.in_(
            select(user_departments.c.department_id).where(
                user_departments.c.user_id == user.id
            )
        ),
    )
    user_hit = exists().where(
        DocumentTarget.document_id == Document.id,
        DocumentTarget.target_type == "user",
        DocumentTarget.user_id == user.id,
    )
    return or_(Document.audience_scope == "all", department_hit, user_hit)


def is_document_visible(
    db: Session, doc: Document, user: User, *, dept_ids: Optional[set[int]] = None
) -> bool:
    """单条判定（详情/文件下载/关联查询用）。"""
    if user.role == User.ROLE_ADMIN:
        return True
    if doc.audience_scope == "all":
        return True
    if dept_ids is None:
        dept_ids = user_department_ids(db, user.id)
    for t in doc.targets:
        if t.target_type == "user" and t.user_id == user.id:
            return True
        if t.target_type == "department" and t.department_id in dept_ids:
            return True
    return False


def apply_targets(db: Session, doc: Document, scope: str, *,
                  department_ids: Optional[list[int]] = None,
                  user_ids: Optional[list[int]] = None) -> None:
    """写入可见范围（覆盖式：先清空再按新范围建行）。

    scope=all 时不写任何目标行——"全体"由 audience_scope 单列表达，
    避免每份公开资料都塞一批冗余行。
    """
    if scope not in AUDIENCE_SCOPES:
        raise ValueError(f"非法可见范围: {scope}")

    doc.audience_scope = scope
    # 覆盖式更新：直接换掉关系集合（cascade delete-orphan 会清旧行）
    doc.targets = []

    if scope == "department":
        for did in dict.fromkeys(department_ids or []):  # 去重且保序
            doc.targets.append(
                DocumentTarget(target_type="department", department_id=did)
            )
    elif scope == "user":
        for uid in dict.fromkeys(user_ids or []):
            doc.targets.append(
                DocumentTarget(target_type="user", user_id=uid)
            )


def scope_label(doc: Document) -> str:
    """给前端/导出用的中文标签。"""
    if doc.audience_scope == "all":
        return "全体可见"
    if doc.audience_scope == "department":
        return "指定部门"
    return "指定人员"
