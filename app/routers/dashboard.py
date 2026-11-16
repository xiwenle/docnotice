"""工作台仪表盘数据（供前端首页使用）。

性能说明：第一版曾对普通用户"逐通知查快照"（N+1），100 通知即 100+ 次查询；
现改为单条聚合查询 + 5 秒 TTL 缓存（读多写少场景，更新后由写操作显式失效）。
"""
from __future__ import annotations

from datetime import date as date_type
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.cache import cache, cache_key
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.backup import BackupRecord
from app.models.document import Document
from app.models.notice import Notice, NoticeRead
from app.models.user import User
from app.services import backup_service, document_visibility
from app.services.notice_common import is_visible_to_user

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

DASH_CACHE_TTL = 5.0

# 缓存失效入口已下沉至 app.core.cache.invalidate_dashboard（2026-08-30，
# 消除 service→router 依赖）；通知侧写操作经 notice_common.invalidate_caches 调用。


@router.get("")
def dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """工作台统计：资料数 / 生效中资料数 / 通知数 / 我的未读通知数（§1）。

    口径（与资料列表一致，项目规范 §14）：
    - document_count：未删除（未归档）的全部资料，对管理员同样排除已删除
    - active_document_count：未删除且按今天计算的"生效中"（闭区间）
    """
    key = cache_key("dashboard", current_user.id, current_user.role)
    hit = cache.get(key)
    if hit is not None:
        return hit

    # 权限模型 v3：全量通知计数仅管理员
    is_admin = current_user.role == User.ROLE_ADMIN
    today = date_type.today()

    # 资料数：未删除，且按当前用户可见范围过滤（2026-11-04 修正）。
    # 此前对普通用户也返回全库计数，导致「全部资料 22 份」但列表里只有 17 份。
    doc_conds = [Document.archived_at.is_(None)]
    vis = document_visibility.visibility_condition(current_user)
    if vis is not None:
        doc_conds.append(vis)

    document_count = db.execute(
        select(func.count(Document.id)).where(*doc_conds)
    ).scalar_one()

    # 生效中资料数：未删除 + 闭区间生效窗口覆盖今天（同口径）
    active_document_count = db.execute(
        select(func.count(Document.id)).where(
            *doc_conds,
            Document.effective_from <= today,
            or_(Document.effective_to.is_(None), Document.effective_to >= today),
        )
    ).scalar_one()

    # 通知数（普通用户：已发布且已生效且 target 含本人）
    now = datetime.now()
    if is_admin:
        # 权限模型 v2：全部通知计数排除已删除（已删除通知统一在回收站）
        notice_count = db.execute(
            select(func.count(Notice.id)).where(Notice.status != "cancelled")
        ).scalar_one()
    else:
        # 单条聚合：取出当前用户全部快照行（含通知状态/生效时间），内存计算可见性
        rows = db.execute(
            select(NoticeRead, Notice)
            .join(Notice, Notice.id == NoticeRead.notice_id)
            .where(NoticeRead.user_id == current_user.id)
        ).all()
        notice_count = sum(
            1
            for read, n in rows
            if is_visible_to_user(n, now)
        )

    # 我的未读（统一口径 2026-09-06 产品决策：仅统计「我是阅读对象」的通知）：
    # 已发布 + 已生效 + 快照行存在（对象内）+ 未读。非对象通知打开详情不置读，
    # 旧 LEFT JOIN 口径会让管理员工作台挂一组永远清不掉的未读数字。
    # 与通知列表 unread_only 过滤严格一致。
    now = datetime.now()
    unread_rows = db.execute(
        select(Notice.id)
        .join(
            NoticeRead,
            and_(NoticeRead.notice_id == Notice.id, NoticeRead.user_id == current_user.id),
        )
        .where(
            Notice.status == "published",
            or_(Notice.effective_at.is_(None), Notice.effective_at <= now),
            NoticeRead.read_at.is_(None),
        )
    ).all()
    my_unread_notices = len(unread_rows)

    payload: dict[str, object] = {
        "document_count": document_count,
        "active_document_count": active_document_count,
        "notice_count": notice_count,
        "my_unread_notices": my_unread_notices,
    }

    # 最近备份时间（仅管理员）：供工作台 sparse 态的健康行显示「最近备份」。
    # 2026-11-03 起「管理待办」五卡已移除（产品决策：管理员不需要系统提醒该做什么），
    # 因此未分类/未打标签/30 天内失效/草稿通知四个聚合计数一并删除，不再查询。
    if is_admin:
        last_backup_at = db.execute(
            select(func.max(BackupRecord.created_at)).where(
                BackupRecord.status.in_(("completed", "restored"))
            )
        ).scalar_one_or_none()
        payload.update({
            "last_backup_at": (
                last_backup_at.isoformat() if last_backup_at else None
            ),
            "backup_stale_days": backup_service.backup_stale_days(),
        })

    cache.set(key, payload, DASH_CACHE_TTL)
    return payload