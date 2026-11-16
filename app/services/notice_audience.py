"""通知阅读对象：展开 / 成员判定 / 快照补建（AGENTS §21）。

2026-10-26 从 notice_service.py 按域拆分而来。本模块只依赖 models，不依赖
通知 CRUD，是列表、详情、发布、用户启用等多条链路的公共底座。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError
from app.models.notice import Notice, NoticeRead
from app.models.user import User, user_departments
from app.schemas.notice import NoticeTarget
from app.services.notice_common import (
    invalidate_caches,
    is_visible_to_user,
    my_snapshot,
)


def build_audience(db: Session, target: NoticeTarget) -> list[User]:
    """按阅读对象规则展开为具体用户集合（并集去重，仅激活用户）。

    **管理员不进阅读对象**（2026-11-03 产品决策）：
    管理员能查看全部通知是管理权限，不代表"被要求阅读"。把管理员算进
    阅读对象会让工作台给他挂一条永远清不掉的未读，也会让统计分母失真。

    注意：这里排除的是"物化快照"，不是可见性——管理员仍能看全部通知
    （见 notice_read.list_notices 的 admin 分支）。
    """
    user_ids: set[int] = set()
    if target.scope == "all":
        rows = db.execute(
            select(User.id).where(
                User.is_active.is_(True),
                User.role != User.ROLE_ADMIN,
            )
        ).scalars().all()
        user_ids.update(rows)
    elif target.scope == "department":
        dept_ids = target.department_ids
        rows = db.execute(
            select(user_departments.c.user_id)
            .where(user_departments.c.department_id.in_(dept_ids))
        ).scalars().all()
        user_ids.update(rows)
    elif target.scope == "user":
        user_ids.update(target.user_ids)

    if not user_ids:
        return []
    # 统一在出口处排除管理员：无论 all / department / user
    # 哪种 scope，管理员都不进入阅读对象（2026-11-03 决策）
    users = db.execute(
        select(User).where(
            User.id.in_(user_ids),
            User.is_active.is_(True),
            User.role != User.ROLE_ADMIN,
        )
    ).scalars().all()
    return list(users)



def _user_department_ids(db: Session, user_id: int) -> set[int]:
    return set(
        db.execute(
            select(user_departments.c.department_id).where(
                user_departments.c.user_id == user_id
            )
        ).scalars().all()
    )



def user_department_ids(db: Session, user_id: int) -> set[int]:
    """当前用户所属部门 id 集合（2026-09-29 新增：供批量目标判定预取复用）。

    单点判定用 is_user_in_target 即可；批量场景（如资料详情的关联通知列表）
    先取一次再逐条传入 dept_ids，避免每条通知各查一次部门表。
    """
    return _user_department_ids(db, user_id)



def is_user_in_target(
    db: Session,
    notice: Notice,
    user: User,
    *,
    dept_ids: Optional[set[int]] = None,
) -> bool:
    """按 notice_targets 规则判断用户是否属于阅读对象（与发布物化口径一致）。

    - target_type=all → 全体；
    - user → 命中 user_id；
    - department → 命中用户所属部门（dept_ids 可由调用方预取复用）；
    - 无目标行 → 与 audience_target 兜底一致，视为全体。
    """
    targets = notice.targets
    if not targets:
        return True
    for t in targets:
        if t.target_type == "all":
            return True
        if t.target_type == "user" and t.user_id == user.id:
            return True
        if t.target_type == "department":
            if dept_ids is None:
                dept_ids = _user_department_ids(db, user.id)
            if t.department_id in dept_ids:
                return True
    return False



def backfill_notice_reads(db: Session, *, user: User) -> int:
    """补建该用户缺失的阅读快照（幂等，返回补建条数；无缺口不写库）。

    场景：通知**发布之后**才创建的用户 / 发布时停用后来启用的用户 /
    发布后调入阅读部门的用户——发布时物化的 NoticeRead 快照不含他们，
    导致永远看不到"全体用户"通知，阅读统计也漏计。
    口径与 publish_notice 物化完全一致：status=published 且按规则属于阅读对象
    （effective_at 未到的通知发布时同样物化，这里保持同一口径）。

    **管理员不补建**（2026-11-03）：管理员不进阅读对象，否则他一登录就会
    被补出一条未读快照，工作台又出现清不掉的"未读 1"。
    """
    if user.role == User.ROLE_ADMIN:
        return 0
    notices = db.execute(
        select(Notice)
        .options(selectinload(Notice.targets))
        .where(Notice.status == "published")
    ).scalars().all()
    if not notices:
        return 0
    existing = set(
        db.execute(
            select(NoticeRead.notice_id).where(NoticeRead.user_id == user.id)
        ).scalars().all()
    )
    dept_ids: Optional[set[int]] = None
    created = 0
    for n in notices:
        if n.id in existing:
            continue
        if dept_ids is None:
            dept_ids = _user_department_ids(db, user.id)
        if not is_user_in_target(db, n, user, dept_ids=dept_ids):
            continue
        db.add(NoticeRead(notice_id=n.id, user_id=user.id))
        created += 1
    if created:
        db.commit()
        invalidate_caches()
    return created



def _ensure_snapshot(
    db: Session, notice: Notice, user: User
) -> Optional[NoticeRead]:
    """取本人快照；缺失但按规则属于阅读对象时惰性补建（兜底防线）。

    覆盖"持有效旧 Token 直接访问（未触发登录事件）"的场景；
    非阅读对象返回 None，由调用方决定 403。

    **管理员不补建**（2026-11-03）：与 build_audience 同口径。
    管理员看通知走 list/detail 的 admin 分支，不需要快照。
    """
    row = my_snapshot(db, notice.id, user.id)
    if row is not None or notice.status != "published":
        return row
    if user.role == User.ROLE_ADMIN:
        return None
    if is_user_in_target(db, notice, user):
        row = NoticeRead(notice_id=notice.id, user_id=user.id)
        db.add(row)
        db.commit()
        invalidate_caches()
    return row



def require_user_target(db: Session, notice: Notice, user: User) -> NoticeRead:
    """用户必须是该通知的实际阅读对象（快照行存在），否则 403。

    快照缺失但按规则属于阅读对象时由 _ensure_snapshot 惰性补建
    （发布后新用户/启用/调入部门场景），保证详情/阅读/确认可用。
    """
    if not is_visible_to_user(notice):
        raise AppError("FORBIDDEN", "通知未发布或尚未生效，无权访问")
    row = _ensure_snapshot(db, notice, user)
    if row is None:
        raise AppError("FORBIDDEN", "你不是该通知的阅读对象")
    return row
