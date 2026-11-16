"""通知出站对象（API 响应 dict）序列化（docs/API.md §7）。

2026-10-26 从 notice_service.py 按域拆分而来。单独成模块，使列表 / 详情 /
创建 / 更新四处共用同一份字段口径，不会各写一份导致漂移。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.notice import Notice
from app.models.user import Department, User
from app.services import document_visibility
from app.services.notice_audience import is_user_in_target
from app.services.notice_common import my_snapshot


def notice_to_dict(
    db: Session,
    notice: Notice,
    user: User,
    *,
    detailed: bool = False,
    read_stats: Optional[dict] = None,
) -> dict:
    row = my_snapshot(db, notice.id, user.id)
    data = {
        "id": notice.id,
        "title": notice.title,
        "notice_type": notice.notice_type,
        "status": notice.status,
        "published_at": notice.published_at,
        "effective_at": notice.effective_at,
        "deadline_at": notice.deadline_at,
        "has_file": notice.file is not None,
        "my_read_at": row.read_at if row else None,
        # 2026-11-03：确认功能移除，阅读状态只剩「已查看/未查看」二值
        "my_read_status": "read" if row and row.read_at else "unread",
        # 是否阅读对象（快照行存在=已物化对象）
        "is_target": row is not None,
    }
    if notice.file:
        data["file_type"] = notice.file.file_type
    if read_stats is not None:
        # 管理视角阅读概要（列表行内已读比例；一次 group by 聚合，非逐条查询）
        data["read_stats"] = read_stats
    if detailed:
        data["content"] = notice.content
        # 是否阅读对象：快照行存在，或按对象规则命中（与 require_user_target
        # 同口径，覆盖发布后新账号快照未补建场景）。
        # 2026-11-03：管理员永远不是阅读对象（不建快照、不进统计），
        # 否则详情页会给他渲染「未查看」徽章，看着像还有事没做。
        if user.role == User.ROLE_ADMIN:
            data["in_audience"] = False
        else:
            data["in_audience"] = row is not None or is_user_in_target(db, notice, user)
        # 已删除（回收站中）的资料不再作为关联资料展示（admin 保留全量视角）。
        # 2026-11-16：普通用户还必须满足资料自身可见范围，否则通知关联列表会
        # 泄露无权资料的标题（直接访问同一资料是 404）。
        docs = notice.documents
        if user.role != "admin":
            dept_ids = document_visibility.user_department_ids(db, user.id)
            docs = [
                d for d in docs
                if d.archived_at is None
                and document_visibility.is_document_visible(db, d, user, dept_ids=dept_ids)
            ]
        data["documents"] = [
            {"id": d.id, "title": d.title, "archived": d.archived_at is not None}
            for d in docs
        ]
        data["audience_meta"] = _audience_meta(db, notice)
    return data



def _audience_meta(db: Session, notice: Notice) -> dict:
    dept_ids = [t.department_id for t in notice.targets if t.department_id is not None]
    user_ids = [t.user_id for t in notice.targets if t.user_id is not None]
    scope = "all"
    if notice.targets:
        scope = notice.targets[0].target_type
    meta: dict = {"scope": scope, "department_ids": dept_ids, "user_ids": user_ids}
    # 展示用名称（详情页显示姓名，编辑表单回填仍用 *_ids）；已删除用户/部门查不到
    # 则列表短一截，前端按 ID 兜底显示
    if user_ids:
        users = db.execute(select(User).where(User.id.in_(user_ids))).scalars().all()
        by_id = {u.id: (u.display_name or u.username) for u in users}
        meta["user_names"] = [by_id[i] for i in user_ids if i in by_id]
    if dept_ids:
        depts = db.execute(select(Department).where(Department.id.in_(dept_ids))).scalars().all()
        by_id = {d.id: d.name for d in depts}
        meta["department_names"] = [by_id[i] for i in dept_ids if i in by_id]
    return meta
