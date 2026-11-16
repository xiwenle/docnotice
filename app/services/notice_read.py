"""通知查询与阅读侧：列表 / 详情 / 置读 / 确认 / 统计 / 名单 / 文件可见性。

2026-10-26 从 notice_service.py 按域拆分而来（AGENTS §20–§21）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Optional

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.session import total_count
from app.models.notice import Notice, NoticeRead
from app.models.user import Department, User, user_departments
from app.schemas.common import page_meta
from app.services import audit_service, document_visibility, validity_service
from app.services.notice_audience import (
    backfill_notice_reads,
    require_user_target,
)
from app.services.notice_common import (
    invalidate_caches,
    load_notice,
    user_departments_map,
)
from app.services.notice_serializer import notice_to_dict

if TYPE_CHECKING:
    from app.models.document import FileRecord


def list_notices(
    db: Session,
    *,
    user: User,
    notice_type: Optional[str] = None,
    unread_only: bool = False,
    status_filter: Optional[str] = None,
    keyword: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """通知列表（SQL 分页，2026-08-30 对齐资料列表口径，消除 N+1/内存分页）：
    - user：仅已发布且已生效且 target 含本人（快照 EXISTS 子查询过滤）
    - admin+：全部（可 status/type/keyword/发布时间范围过滤）
    - date_from/date_to：published_at 闭区间（date_to 含当日全天，前端传纯日期）
    - unread_only：按当前用户快照的 read_at 判定
    """
    # 权限模型 v3：管理视图（全部通知含草稿/已取消）仅管理员
    is_admin = user.role == User.ROLE_ADMIN
    # date_to 语义为"含当日"，过滤时右界取次日零点（开区间）
    date_to_excl = date_to + timedelta(days=1) if date_to else None

    if not is_admin:
        # 惰性补建（幂等）：发布后新创建/启用/调入部门的用户在此自动获得
        # 缺失的阅读快照，保证通知列表与工作台计数一致正确（兜底防线）
        backfill_notice_reads(db, user=user)

    # 快照 EXISTS 子查询：一用户一通知至多一行（唯一约束），单个子查询即可
    # 同时表达"是阅读对象"与 read_at 状态
    def _snap(extra=None):
        conds = [NoticeRead.notice_id == Notice.id, NoticeRead.user_id == user.id]
        if extra is not None:
            conds.append(extra)
        return exists().where(*conds)

    conds = []
    if is_admin:
        # 已删除（cancelled）通知不进入通知列表——统一在回收站查看/恢复
        conds.append(Notice.status != "cancelled")
        if status_filter:
            conds.append(Notice.status == status_filter)
    else:
        # 与 is_visible_to_user 逐条等价的 SQL 形式：published 且已生效
        conds.append(Notice.status == "published")
        conds.append(or_(Notice.effective_at.is_(None), Notice.effective_at <= datetime.now()))
        conds.append(_snap())

    if unread_only:
        # 未读口径（与工作台 my_unread_notices 严格一致，2026-09-06 产品决策）：
        # 仅统计「我是阅读对象（快照存在）且未读」的通知，所有角色同口径；
        # 非对象通知打开详情不置读，计入只会产生永远清不掉的未读数字
        conds.append(Notice.status == "published")
        conds.append(or_(Notice.effective_at.is_(None), Notice.effective_at <= datetime.now()))
        conds.append(_snap(NoticeRead.read_at.is_(None)))
    if notice_type == "__unclassified__":
        conds.append(or_(Notice.notice_type.is_(None), Notice.notice_type == ""))
    elif notice_type:
        conds.append(Notice.notice_type == notice_type)
    if keyword:
        like = f"%{keyword}%"
        conds.append(or_(Notice.title.like(like), Notice.content.like(like)))
    if date_from:
        conds.append(Notice.published_at >= date_from)
    if date_to_excl:
        conds.append(Notice.published_at < date_to_excl)

    total = total_count(db, select(Notice.id).where(*conds))
    page_notices = db.execute(
        select(Notice)
        .where(*conds)
        .order_by(Notice.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()

    # 管理视角：一次 group by 聚合本页通知的阅读/确认概要（避免逐条 N+1）
    stats_map: dict[int, dict] = {}
    if is_admin and page_notices:
        ids = [n.id for n in page_notices]
        rows = db.execute(
            select(
                NoticeRead.notice_id,
                func.count().label("target"),
                func.count(NoticeRead.read_at).label("viewed"),
            )
            .join(User, User.id == NoticeRead.user_id)
            .where(
                NoticeRead.notice_id.in_(ids),
                User.is_active.is_(True),  # 分母与统计页同口径：仅实际应阅读的启用用户
            )
            .group_by(NoticeRead.notice_id)
        ).all()
        stats_map = {
            r.notice_id: {"target": r.target, "viewed": r.viewed}
            for r in rows
        }

    items = [
        notice_to_dict(db, n, user, read_stats=stats_map.get(n.id))
        for n in page_notices
    ]
    return {"items": items, **page_meta(total, page, page_size)}



def get_notice(db: Session, *, user: User, notice_id: int) -> dict:
    """通知详情（不自动置读，§20；user 仅 target 内可见）。"""
    notice = load_notice(db, notice_id)
    # 已删除通知不在通知列表和详情页显示——统一在回收站管理
    if notice.status == "cancelled":
        raise AppError("NOT_FOUND", "该通知已移至回收站")
    if user.role != "admin":
        require_user_target(db, notice, user)
    return notice_to_dict(db, notice, user, detailed=True)



def mark_read(
    db: Session,
    *,
    user: User,
    notice_id: int,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """标记已查看：置 read_at（首次覆盖，幂等；§20）。

    管理员不是阅读对象（2026-11-03 决策），没有快照。他打开详情页时前端
    照常调用本接口，这里直接返回成功且不写库——不能 403，否则管理员每次
    看通知都会在控制台留一条权限错误，且页面状态异常。
    """
    notice = load_notice(db, notice_id)
    if notice.status == "cancelled":
        raise AppError("NOT_FOUND", "该通知已移至回收站")
    if user.role == User.ROLE_ADMIN:
        return {"my_read_at": None, "skipped": "admin"}
    row = require_user_target(db, notice, user)
    if row.read_at is None:
        row.read_at = datetime.now()
        row.read_ip = ip
        audit_service.log(
            db,
            action=audit_service.ACTION_READ_NOTICE,
            user=user,
            object_type="notice",
            object_id=notice.id,
            object_label=notice.title,
            ip=ip,
            user_agent=user_agent,
        )
        db.commit()
        invalidate_caches()
    return {"my_read_at": row.read_at}



def get_statistics(db: Session, *, user: User, notice_id: int) -> dict:
    """阅读统计（§21）：仅统计「实际需阅读的人」。

    口径（2026-11-03 修订）：
    - 只统计快照行（发布时物化的应读名单），不统计管理员——管理员能查看全部
      通知是管理权限，不代表他"被要求阅读"，计入分母只会让完成率永远差几个人。
    - 排除已停用用户（§21：发布后停用的用户不应计入分母）。
    - 「确认」功能已移除，只保留已查看 / 未查看单一维度。

    性能：单条 SQL 聚合（COUNT + 条件 COUNT + 部门分组子查询），
    避免单通知数十万快照时全量读入内存。
    """
    notice = load_notice(db, notice_id)

    # 管理员不进统计分母：User.role != 'admin'
    base_filter = (
        NoticeRead.notice_id == notice_id,
        User.is_active.is_(True),
        User.role != User.ROLE_ADMIN,
    )

    total, viewed = db.execute(
        select(
            func.count(NoticeRead.id),
            func.count(NoticeRead.read_at),
        )
        .join(User, User.id == NoticeRead.user_id)
        .where(*base_filter)
    ).one()
    target_user_count = total or 0
    viewed_count = viewed or 0
    unviewed_count = target_user_count - viewed_count

    # 按部门分组（多部门用户在各部门都计入；全局总数无重复）
    by_department: list[dict] = []
    if target_user_count:
        dept_rows = db.execute(
            select(
                user_departments.c.department_id,
                func.group_concat(user_departments.c.user_id),
            )
            .join(NoticeRead, NoticeRead.user_id == user_departments.c.user_id)
            .join(User, User.id == NoticeRead.user_id)
            .where(*base_filter)
            .group_by(user_departments.c.department_id)
        ).all()
        for dept_id, user_ids_csv in dept_rows:
            ids = {int(x) for x in user_ids_csv.split(",") if x}
            dept_viewed = db.execute(
                select(func.count(NoticeRead.read_at)).where(
                    NoticeRead.notice_id == notice_id,
                    NoticeRead.user_id.in_(ids),
                )
            ).scalar_one()
            dept = db.get(Department, dept_id)
            by_department.append({
                "department_id": dept_id,
                "name": dept.name if dept else "",
                "target": len(ids),
                "viewed": dept_viewed or 0,
                "unviewed": len(ids) - (dept_viewed or 0),
            })

    return {
        "notice_id": notice.id,
        "target_user_count": target_user_count,
        "viewed_count": viewed_count,
        "unviewed_count": unviewed_count,
        "viewed_rate": round(viewed_count / target_user_count, 4) if target_user_count else 0,
        "by_department": by_department,
    }


def get_audience(
    db: Session,
    *,
    user: User,
    notice_id: int,
    read_status: Optional[str] = None,
    keyword: Optional[str] = None,
    department_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """应阅读名单（含查看时间），供管理员核对「谁还没看」。

    口径与阅读统计严格一致：只列启用用户、排除管理员（§21 + 2026-11-03 决策）。

    keyword：按用户名 / 显示名 / 部门名模糊搜索——名单可能上百人，
    滚动找人不现实，必须能直接搜。
    department_id：从阅读统计「按部门」行下钻；多部门用户在对应每个部门
    名单中都会出现，部门过滤在分页前完成。
    """
    load_notice(db, notice_id)
    stmt = (
        select(NoticeRead)
        .join(User, User.id == NoticeRead.user_id)
        .where(
            NoticeRead.notice_id == notice_id,
            User.is_active.is_(True),
            User.role != User.ROLE_ADMIN,
        )
    )
    if department_id is not None:
        stmt = stmt.where(
            exists().where(
                user_departments.c.user_id == NoticeRead.user_id,
                user_departments.c.department_id == department_id,
            )
        )
    rows = db.execute(stmt).scalars().all()

    def _match_status(r: NoticeRead) -> bool:
        if read_status in (None, "all"):
            return True
        if read_status == "read":
            return r.read_at is not None
        if read_status == "unread":
            return r.read_at is None
        return True

    rows = [r for r in rows if _match_status(r)]
    total = len(rows)
    page_rows = rows[(page - 1) * page_size: page * page_size]

    users = db.execute(
        select(User).where(User.id.in_([r.user_id for r in page_rows]))
    ).scalars().all() if page_rows else []
    user_map = {u.id: u for u in users}
    dept_map = user_departments_map(db, [u.id for u in users])

    items = []
    for r in page_rows:
        u = user_map.get(r.user_id)
        items.append({
            "user_id": r.user_id,
            "username": u.username if u else "",
            "display_name": u.display_name if u else "",
            "department_names": dept_map.get(r.user_id, []),
            "read_at": r.read_at,
        })

    # keyword 过滤放在取页之后会漏人，因此在分页前按人过滤
    if keyword:
        kw = keyword.strip().lower()
        if kw:
            # 需要先按人过滤，再重新分页——重查一次，保证 total 与页内容一致
            all_users = db.execute(
                select(User).where(User.id.in_([r.user_id for r in rows]))
            ).scalars().all() if rows else []
            all_map = {u.id: u for u in all_users}
            all_depts = user_departments_map(db, [u.id for u in all_users])

            def _hit(r: NoticeRead) -> bool:
                u = all_map.get(r.user_id)
                if not u:
                    return False
                hay = " ".join([
                    u.username or "", u.display_name or "",
                    " ".join(all_depts.get(r.user_id, [])),
                ]).lower()
                return kw in hay

            rows = [r for r in rows if _hit(r)]
            total = len(rows)
            page_rows = rows[(page - 1) * page_size: page * page_size]
            users = [all_map[r.user_id] for r in page_rows if r.user_id in all_map]
            dept_map = all_depts
            items = []
            for r in page_rows:
                u = all_map.get(r.user_id)
                items.append({
                    "user_id": r.user_id,
                    "username": u.username if u else "",
                    "display_name": u.display_name if u else "",
                    "department_names": dept_map.get(r.user_id, []),
                    "read_at": r.read_at,
                })

    return {
        "items": items,
        "department_id": department_id,
        **page_meta(total, page, page_size),
    }



def get_linked_documents(db: Session, *, user: User, notice_id: int) -> dict:
    """通知关联资料（§19；已删除（回收站中）资料仅 admin 可见）。

    2026-11-16：非管理员还必须满足资料自身的可见范围（audience_scope /
    document_targets）。此前只过滤归档，导致「通知发给全体、关联了仅某部门/某人
    可见的资料」时，其他普通用户能从关联列表拿到该资料的 id/标题/状态，而直接
    访问该资料是 404——元数据越权泄露。
    """
    notice = load_notice(db, notice_id)
    if user.role != "admin":
        require_user_target(db, notice, user)
    docs = notice.documents
    if user.role != "admin":
        dept_ids = document_visibility.user_department_ids(db, user.id)
        docs = [
            d for d in docs
            if d.archived_at is None
            and document_visibility.is_document_visible(db, d, user, dept_ids=dept_ids)
        ]
    items = []
    for doc in docs:
        items.append({
            "id": doc.id,
            "title": doc.title,
            "status": validity_service.compute_status(
                doc.effective_from, doc.effective_to, date.today(),
                archived=doc.archived_at is not None,
            ),
        })
    return {"items": items}



def notice_file_visible(db: Session, *, user: User, notice_id: int) -> "tuple[Notice, FileRecord]":
    """通知文件可见性：返回 (notice, file_record)；不可见抛 403。"""
    notice = load_notice(db, notice_id)
    if user.role != "admin":
        require_user_target(db, notice, user)
    if notice.file is None:
        raise AppError("NOT_FOUND", "通知无文件")
    return notice, notice.file
