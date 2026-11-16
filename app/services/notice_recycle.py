"""通知回收站：取消 / 恢复 / 彻底删除 / 草稿删除（AGENTS §26）。

2026-10-26 从 notice_service.py 按域拆分而来。
"""
from __future__ import annotations

from typing import Optional, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.notice import Notice, NoticeRead
from app.models.user import User
from app.services import audit_service
from app.services.notice_common import invalidate_caches, load_notice
from app.services.notice_linkage import reconcile_auto_expire_linkage


def _cancel_notice_in_tx(
    db: Session,
    *,
    current_user: User,
    notice_id: int,
    reason: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """在调用方事务内取消一条通知（批量操作的内层复用实现）。"""
    notice = load_notice(db, notice_id)

    # 并发保护（2026-11-06 实测发现）：原先是「读 status → 判断 → 改」的
    # check-then-act，两个并发取消请求都能读到 published 并双双提交，
    # 结果两条都返回 200（实测 [200, 200]）。这里改成**带条件的原子 UPDATE**：
    # 只有当前仍是 published 的行才会被改，affected=0 说明已被别的请求取消。
    updated = cast(CursorResult, db.execute(
        update(Notice)
        .where(Notice.id == notice_id, Notice.status == "published")
        .values(
            status="cancelled",
            cancel_reason=(reason or "").strip() or "管理员删除",
            updated_by=current_user.id,
        )
    )).rowcount
    if updated == 0:
        raise AppError("CONFLICT", "通知已在回收站")

    # 把 ORM 对象同步到刚写入的值，后续联动与审计读的是内存态
    db.refresh(notice)
    notice.status = "cancelled"
    notice.cancel_reason = (reason or "").strip() or "管理员删除"
    notice.updated_by = current_user.id
    # 取消 → 联动重算（§31 / L-1）：状态已置 cancelled，期望截断必然为假，
    # 于是把本通知仍持有的资料全部回滚、账本清空。
    reconcile_auto_expire_linkage(
        db,
        notice=notice,
        current_user=current_user,
        via="notice_cancel",
        reason="取消废止/替换通知，回滚资料时效",
        skip_archived=False,
        ip=ip,
        user_agent=user_agent,
    )

    audit_service.log(
        db,
        action=audit_service.ACTION_CANCEL_NOTICE,
        user=current_user,
        object_type="notice",
        object_id=notice.id,
        object_label=notice.title,
        detail={"reason": notice.cancel_reason},
        ip=ip,
        user_agent=user_agent,
    )
    return {
        "ok": True,
        "id": notice.id,
        "title": notice.title,
        "status": "cancelled",
    }


def cancel_notice(
    db: Session,
    *,
    current_user: User,
    notice_id: int,
    reason: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """删除通知（软删除 → 回收站；保留 notice_reads 统计证据；reason 可选）。"""
    try:
        result = _cancel_notice_in_tx(
            db,
            current_user=current_user,
            notice_id=notice_id,
            reason=reason,
            ip=ip,
            user_agent=user_agent,
        )
    except Exception:
        db.rollback()
        raise
    db.commit()
    invalidate_caches(lists=True)
    return result


def bulk_cancel_notices(
    db: Session,
    *,
    current_user: User,
    notice_ids: list[int],
    reason: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """批量软删除通知；逐条返回成功/跳过原因，并写一条汇总审计。

    仅执行既有 cancel 语义，不提供正文、对象、类型或阅读状态的批量修改。
    每条通知使用 savepoint 隔离：某条因不存在/已在回收站而跳过，不影响其它条。
    """
    ids = list(dict.fromkeys(int(i) for i in notice_ids))
    if not ids:
        raise AppError("VALIDATION_ERROR", "notice_ids 不能为空")

    succeeded: list[dict] = []
    skipped: list[dict] = []
    for notice_id in ids:
        savepoint = db.begin_nested()
        try:
            result = _cancel_notice_in_tx(
                db,
                current_user=current_user,
                notice_id=notice_id,
                reason=reason,
                ip=ip,
                user_agent=user_agent,
            )
            savepoint.commit()
            succeeded.append(result)
        except AppError as exc:
            savepoint.rollback()
            skipped.append({"id": notice_id, "reason": exc.message})

    audit_service.log(
        db,
        action=audit_service.ACTION_CANCEL_NOTICE,
        user=current_user,
        object_type="notice",
        object_label=f"批量移入回收站：成功 {len(succeeded)} 条，跳过 {len(skipped)} 条",
        detail={
            "batch": True,
            "via": "notice_bulk_cancel",
            "requested": len(ids),
            "succeeded": [row["id"] for row in succeeded],
            "skipped": skipped,
            "reason": (reason or "").strip() or "管理员批量删除",
        },
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    invalidate_caches(lists=True)
    return {
        "requested": len(ids),
        "succeeded": succeeded,
        "skipped": skipped,
    }


# 2026-11-04：delete_draft_notice 已删除——草稿状态下线后不存在「只能删草稿」
# 的路径；删除通知统一走 cancel_notice（已发布 → 回收站，可恢复）。


def list_deleted_notices(db: Session) -> dict:
    """回收站-已删除通知列表（cancelled，新→旧）。"""
    rows = db.execute(
        select(Notice)
        .where(Notice.status == "cancelled")
        .order_by(Notice.id.desc())
    ).scalars().all()
    items = []
    for n in rows:
        # 「已读」= 已查看人数（与阅读统计同口径：只算启用用户）
        read_count = db.execute(
            select(func.count(NoticeRead.read_at))
            .join(User, User.id == NoticeRead.user_id)
            .where(
                NoticeRead.notice_id == n.id,
                User.is_active.is_(True),
            )
        ).scalar_one()
        items.append({
            "id": n.id,
            "title": n.title,
            "notice_type": n.notice_type,
            "published_at": n.published_at,
            "cancel_reason": n.cancel_reason,
            "has_file": n.file is not None,
            "read_count": read_count,
        })
    return {"items": items, "total": len(items)}



def _reapply_auto_expire_on_restore(
    db: Session,
    *,
    notice: Notice,
    current_user: User,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """恢复废止/替换通知时，重新联动关联资料失效（与发布共用同一实现）。

    删除通知时曾把资料 effective_to 回滚（L-1）；若恢复后不重新应用，
    会出现「废止通知已恢复可见，但被废止资料仍显示有效」的矛盾状态。
    防护规则与发布时一致，仅额外跳过已归档资料。
    """
    reconcile_auto_expire_linkage(
        db,
        notice=notice,
        current_user=current_user,
        via="notice_restore",
        reason="恢复废止/替换通知，重新联动资料失效",
        skip_archived=True,
        ip=ip,
        user_agent=user_agent,
    )



def _restore_notice_in_tx(
    db: Session,
    *,
    current_user: User,
    notice_id: int,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """在调用方事务内恢复一条通知（批量操作的内层复用实现）。"""
    notice = load_notice(db, notice_id)
    if notice.status != "cancelled":
        raise AppError("CONFLICT", "仅回收站中的通知可恢复")
    notice.status = "published"
    notice.updated_by = current_user.id
    _reapply_auto_expire_on_restore(
        db, notice=notice, current_user=current_user, ip=ip, user_agent=user_agent
    )
    audit_service.log(
        db,
        action=audit_service.ACTION_RECYCLE_RESTORE,
        user=current_user,
        object_type="notice",
        object_id=notice.id,
        object_label=notice.title,
        detail={"via": "notice_recycle_restore", "restored_as": notice.status},
        ip=ip,
        user_agent=user_agent,
    )
    return {
        "ok": True,
        "id": notice.id,
        "title": notice.title,
        "status": notice.status,
    }


def restore_notice(
    db: Session,
    *,
    current_user: User,
    notice_id: int,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """从回收站恢复通知：恢复为 published，保留原发布时间与阅读统计；
    废止/替换类通知重新联动关联资料失效（与发布语义对称，防状态矛盾）。

    2026-11-04：草稿下线后不再有「恢复回草稿」分支——所有通知都经过发布，
    阅读快照在建库事务里就已物化，恢复不会出现「已发布却没人看得到」。
    """
    try:
        result = _restore_notice_in_tx(
            db,
            current_user=current_user,
            notice_id=notice_id,
            ip=ip,
            user_agent=user_agent,
        )
    except Exception:
        db.rollback()
        raise
    db.commit()
    invalidate_caches(lists=True)
    return result


def bulk_restore_notices(
    db: Session,
    *,
    current_user: User,
    notice_ids: list[int],
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """批量恢复回收站通知；逐条返回成功/跳过原因，并写一条汇总审计。

    每条均复用既有 restore 逻辑，废止/替换联动照常重新应用。
    """
    ids = list(dict.fromkeys(int(i) for i in notice_ids))
    if not ids:
        raise AppError("VALIDATION_ERROR", "notice_ids 不能为空")

    succeeded: list[dict] = []
    skipped: list[dict] = []
    for notice_id in ids:
        savepoint = db.begin_nested()
        try:
            result = _restore_notice_in_tx(
                db,
                current_user=current_user,
                notice_id=notice_id,
                ip=ip,
                user_agent=user_agent,
            )
            savepoint.commit()
            succeeded.append(result)
        except AppError as exc:
            savepoint.rollback()
            skipped.append({"id": notice_id, "reason": exc.message})

    audit_service.log(
        db,
        action=audit_service.ACTION_RECYCLE_RESTORE,
        user=current_user,
        object_type="notice",
        object_label=f"批量恢复通知：成功 {len(succeeded)} 条，跳过 {len(skipped)} 条",
        detail={
            "batch": True,
            "via": "notice_bulk_restore",
            "requested": len(ids),
            "succeeded": [row["id"] for row in succeeded],
            "skipped": skipped,
        },
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    invalidate_caches(lists=True)
    return {
        "requested": len(ids),
        "succeeded": succeeded,
        "skipped": skipped,
    }



def purge_notice(
    db: Session,
    *,
    current_user: User,
    notice_id: int,
    confirm: bool = False,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """彻底删除回收站中的通知（物理删除；confirm=true 二次确认；§26 唯一物理删除入口）。

    清理：阅读快照 / 阅读对象 / 资料关联 / 附件记录；附件物理文件移入回收站目录留档。
    """
    if not confirm:
        raise AppError("VALIDATION_ERROR", "请传 confirm=true 二次确认")
    notice = load_notice(db, notice_id)
    if notice.status != "cancelled":
        raise AppError("CONFLICT", "仅回收站中的通知可彻底删除（请先删除）")

    title = notice.title
    file_record = notice.file

    # 1) 附件物理文件移入回收站目录留档（不直接物理删除，§26）
    if file_record is not None:
        from app.core.config import get_settings
        from app.services import file_service as _fs

        src = _fs.abs_path(file_record.storage_rel_path)
        if src.exists():
            recycle_dir = get_settings().recycle_bin_dir
            recycle_dir.mkdir(parents=True, exist_ok=True)
            target = recycle_dir / file_record.internal_name
            try:
                src.replace(target)
            except OSError:
                pass  # 移动失败不阻断通知行删除（文件留在原处由运维处理）

    # 2) 清理关联行（快照/对象/资料关联）
    # 2026-09-29 修复：原实现先 bulk delete 子行、再 db.delete(notice)，但
    # Notice.targets / Notice.reads 是 cascade="all, delete-orphan"——ORM 会对集合里
    # 仍持有的子对象**再发一次 DELETE**，此刻行已被删掉，命中 0 行 →
    # SAWarning「expected to delete 1 row(s); 0 were matched」（purge-all 与单条
    # 彻底删除都会触发）。改为清空关系集合，让级联只删一次。
    notice.reads.clear()
    notice.targets.clear()
    notice.documents = []
    # 3) 删除通知行（附件记录随行外键删除或置空后删除）
    if file_record is not None:
        db.delete(file_record)
    db.delete(notice)

    audit_service.log(
        db,
        action=audit_service.ACTION_RECYCLE_PURGE,
        user=current_user,
        object_type="notice",
        object_id=None,
        object_label=title,
        detail={"via": "notice_recycle_purge", "had_file": file_record is not None},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    invalidate_caches(lists=True)
    return {"ok": True, "id": notice_id, "purged": True}
