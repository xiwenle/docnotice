"""通知服务核心：创建（=发布） / 修改（AGENTS §17–§22）。

2026-11-04：草稿状态下线，`publish_notice` 已删除——发布逻辑并入
`create_notice` 的单事务（建库 + 物化快照 + 废止/替换联动）。

2026-10-26 从原 1555 行单文件按域拆分后的主模块。其余职责已下沉：
- 共享底层 → notice_common.py
- 时效联动 → notice_linkage.py
- 阅读对象 → notice_audience.py
- 响应序列化 → notice_serializer.py
- 查询与阅读 → notice_read.py
- 回收站 → notice_recycle.py
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.notice import Notice, NoticeRead
from app.models.user import User
from app.schemas.notice import NoticeTarget
from app.services import audit_service, file_service
from app.services.notice_audience import build_audience
from app.services.notice_common import (
    invalidate_caches,
    load_documents,
    load_notice,
    my_snapshot,
    target_to_rows,
    validate_notice_type,
    validate_target_ids,
)
from app.services.notice_linkage import reconcile_auto_expire_linkage
from app.services.notice_serializer import notice_to_dict

logger = logging.getLogger(__name__)


def create_notice(
    db: Session,
    *,
    current_user: User,
    title: str,
    notice_type: Optional[str] = None,
    content: Optional[str] = None,
    effective_at: Optional[datetime] = None,
    deadline_at: Optional[datetime] = None,
    document_ids: Optional[list[int]] = None,
    target: Optional[NoticeTarget] = None,
    file_data: Optional[bytes] = None,
    file_name: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """新建并发布通知（单事务，2026-11-04）。

    草稿状态已下线：创建即发布，一次事务内完成
      1. 建库（notices + notice_targets + notice_documents）
      2. 物化阅读快照（notice_reads）
      3. 废止/替换联动（截断关联资料有效期）
    任一步失败 → 整体回滚，不会留下半成品通知，也不会把资料时效改坏。

    以前这两步分在 POST /notices 和 POST /notices/{id}/publish：发布失败时
    通知会残留在草稿态，界面上又没有「存为草稿」入口，成了没人认领的孤儿。
    """
    notice_type = (notice_type or "").strip() or None
    if notice_type:
        validate_notice_type(db, notice_type)
    title = (title or "").strip()
    if not title:
        raise AppError("VALIDATION_ERROR", "标题不能为空")
    if not content and not file_data:
        raise AppError("VALIDATION_ERROR", "通知必须有正文或原始文件（可两者兼有）")

    target = target or NoticeTarget(scope="all")
    validate_target_ids(db, target)

    # 阅读对象必须先算出非空，否则发布没有意义（§21）
    audience = build_audience(db, target)
    if not audience:
        raise AppError("VALIDATION_ERROR", "阅读对象范围为空，无法发布")

    docs = load_documents(db, document_ids or [])

    notice = Notice(
        title=title,
        content=content,
        notice_type=notice_type,
        status="published",
        published_at=datetime.now(),
        effective_at=effective_at,
        deadline_at=deadline_at,
        created_by=current_user.id,
    )
    notice.documents = docs
    for t in target_to_rows(target):
        notice.targets.append(t)
    db.add(notice)
    db.flush()

    if file_data is not None:
        rec = file_service.save_upload(
            db,
            owner_type="notice",
            owner_id=notice.id,
            original_filename=file_name or "通知文件",
            data=file_data,
            uploaded_by=current_user,
        )
        notice.file_id = rec.id

    # 物化阅读快照：与旧 publish_notice 完全相同的口径
    for u in audience:
        if my_snapshot(db, notice.id, u.id) is None:
            db.add(NoticeRead(notice_id=notice.id, user_id=u.id))

    # 废止/替换联动（§31）：冲突 → 异常回滚整个创建，通知也不会留下
    reconcile_auto_expire_linkage(
        db,
        notice=notice,
        current_user=current_user,
        via="notice_publish",
        reason="废止/替换通知导致资料失效",
        skip_archived=False,
        ip=ip,
        user_agent=user_agent,
    )

    audit_service.log(
        db,
        action=audit_service.ACTION_CREATE_NOTICE,
        user=current_user,
        object_type="notice",
        object_id=notice.id,
        object_label=notice.title,
        detail={
            "notice_type": notice_type,
            "document_ids": document_ids or [],
            "target_scope": target.scope,
            "has_file": file_data is not None,
            "target_user_count": len(audience),
        },
        ip=ip,
        user_agent=user_agent,
    )
    # 发布动作单独留一条审计：创建与发布合并后仍要能追溯「何时对谁生效」
    audit_service.log(
        db,
        action=audit_service.ACTION_PUBLISH_NOTICE,
        user=current_user,
        object_type="notice",
        object_id=notice.id,
        object_label=notice.title,
        detail={"target_user_count": len(audience)},
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    invalidate_caches()

    result = notice_to_dict(db, notice, current_user, detailed=True)
    result["target_user_count"] = len(audience)
    return result



def update_notice(
    db: Session,
    *,
    current_user: User,
    notice_id: int,
    title: Optional[str] = None,
    content: Optional[str] = None,
    notice_type: Optional[str] = None,
    effective_at: Optional[datetime] = None,
    deadline_at: Optional[datetime] = None,
    clear_deadline: bool = False,
    document_ids: Optional[list[int]] = None,
    target: Optional[NoticeTarget] = None,
    confirm_modify: bool = False,
    modified_reason: Optional[str] = None,
    file_data: Optional[bytes] = None,
    file_name: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """修改通知（§22 已读后修改强制 confirm_modify + reason）。"""
    notice = load_notice(db, notice_id)
    # 已删除通知不可编辑——统一在回收站管理
    if notice.status == "cancelled":
        raise AppError("NOT_FOUND", "该通知已移至回收站，不可编辑")

    has_read_records = db.execute(
        select(func.count(NoticeRead.id)).where(
            NoticeRead.notice_id == notice_id,
            NoticeRead.read_at.isnot(None),
        )
    ).scalar_one()

    published = notice.status == "published"
    if published and has_read_records > 0:
        if not confirm_modify or not (modified_reason or "").strip():
            raise AppError(
                "NOTICE_MODIFY_REQUIRES_REASON",
                "该通知已发布且已有人查看，修改必须传 confirm_modify=true 并填写 modified_reason",
            )
        notice.modified_reason = modified_reason
        notice.updated_by = current_user.id

    # 已发布通知禁止修改阅读对象（§22 保守：不通过改 target 抵消已记录）
    if published and target is not None:
        raise AppError(
            "CONFLICT",
            "已发布通知禁止修改阅读对象（已产生阅读快照）；如需调整请新建补充通知",
        )

    if title is not None:
        notice.title = title.strip()
    if content is not None:
        notice.content = content
    if notice_type is not None:
        # 允许被显式清空（前端传空串），空类型不触发废止/替换联动
        notice_type = notice_type.strip() or None
        if notice_type:
            validate_notice_type(db, notice_type)
        notice.notice_type = notice_type
    if effective_at is not None:
        notice.effective_at = effective_at
    if clear_deadline:
        notice.deadline_at = None
    elif deadline_at is not None:
        notice.deadline_at = deadline_at
    if document_ids is not None:
        notice.documents = load_documents(db, document_ids)
    if target is not None:
        validate_target_ids(db, target)
        notice.targets.clear()
        notice.targets.extend(target_to_rows(target))

    if file_data is not None:
        # 2026-09-29 修复（P1 · 数据完整性）：顺序必须是
        #   ① 预校验（无任何物理副作用）→ ② 旧文件入回收站 → ③ 新文件落盘
        # 原实现是「先 recycle_file 旧文件，校验藏在 save_upload 内部」：新文件不合法时
        # 异常上抛 → 事务回滚让库记录认为「旧文件仍在原路径」，但磁盘上旧文件已被移进
        # 回收站、RecycleBin 行也随回滚消失 → 该通知的文件凭空消失（下载报不存在、
        # 回收站里也看不到）。对齐 file_service.replace_document_file 的两阶段做法。
        original_filename = file_name or "通知文件"
        meta = file_service.validate_upload(original_filename, file_data)
        old_file = notice.file
        recycle_entry = None
        if old_file is not None:
            recycle_entry = file_service.recycle_file(
                db,
                file_record=old_file,
                reason="replaced",
                deleted_by=current_user,
            )
        try:
            # 传入 meta：内部名已固定，失败时可按同一名字清理孤儿文件
            rec = file_service.save_upload(
                db,
                owner_type="notice",
                owner_id=notice.id,
                original_filename=original_filename,
                data=file_data,
                uploaded_by=current_user,
                meta=meta,
            )
        except Exception:
            # ---- 物理补偿：孤儿清理 + 旧文件回位（DB 由调用方事务回滚）----
            orphan = file_service.save_target_path("notice", meta["internal_name"])
            try:
                if orphan.exists():
                    orphan.unlink()
                    logger.info("通知替换失败补偿：已删除孤儿文件 %s", orphan.name)
            except OSError:
                logger.exception("通知替换失败补偿：孤儿文件清理失败: %s", orphan)
            if old_file is not None and recycle_entry is not None:
                # origin_rel_path 列可空；文件回位必须有原路径，缺失则跳过并留痕
                origin = recycle_entry.origin_rel_path
                if origin is None:
                    logger.warning(
                        "通知替换失败补偿：回收站条目缺 origin_rel_path，跳过旧文件回位（file_record=%s）",
                        old_file.id,
                    )
                else:
                    try:
                        file_service.restore_recycled_file(
                            db,
                            file_record=old_file,
                            origin_rel_path=origin,
                            actor=current_user,
                        )
                        logger.info("通知替换失败补偿：旧文件已移回 %s", origin)
                    except Exception:  # noqa: BLE001
                        logger.exception("通知替换失败补偿：旧文件回位失败")
            raise
        notice.file_id = rec.id

    notice.updated_at = datetime.now()
    if published:
        notice.updated_by = current_user.id
        notice.modified_reason = notice.modified_reason or modified_reason

    # 联动重算（P1 · 时效正确性）：notice_type / effective_at / document_ids 三者
    # 都会改变"废止/替换"的期望效果，必须在此统一对齐，否则出现四种不一致
    # （详见 reconcile_auto_expire_linkage 的 docstring 与
    #  docs/变更确认单-通知类型变更联动-2026-09-29.md）。
    # 顺序要求：必须放在所有字段赋值之后、审计与 commit 之前 —— 这样 R1 冲突
    # 抛出的异常能回滚整个 update，管理员不会拿到"改了一半"的通知。
    reconcile_auto_expire_linkage(
        db,
        notice=notice,
        current_user=current_user,
        via="notice_update",
        reason="已发布通知修改后重算废止/替换联动",
        skip_archived=False,
        ip=ip,
        user_agent=user_agent,
    )

    audit_service.log(
        db,
        action=audit_service.ACTION_UPDATE_NOTICE,
        user=current_user,
        object_type="notice",
        object_id=notice.id,
        object_label=notice.title,
        detail={
            "published": published,
            "had_read_records": has_read_records > 0,
            "confirm_modify": confirm_modify,
            "modified_reason": modified_reason,
        },
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()
    invalidate_caches()
    return notice_to_dict(db, notice, current_user, detailed=True)
