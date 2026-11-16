"""通知 → 资料时效联动（废止/替换语义，DESIGN §7.3、AGENTS §18）。

2026-10-26 从 notice_service.py 按域拆分而来。本模块只依赖 models 与
validity_service，不反向依赖通知 CRUD，故可被 publish/update/cancel/restore
与 notice_type_service 自由复用。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.notice import Notice
from app.models.user import User
from app.services import audit_service, validity_service

# 发布时自动使关联资料失效的类型（DESIGN §7.3）
AUTO_EXPIRE_TYPES = ("废止", "替换")


def reconcile_auto_expire_linkage(
    db: Session,
    *,
    notice: Notice,
    current_user: User,
    via: str,
    reason: str,
    skip_archived: bool = False,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """把关联资料的 effective_to **对齐到本通知当前状态**（幂等，可反复调用）。

    2026-09-29 新增（P1 · 时效正确性，产品决策方案 B+，见
    docs/变更确认单-通知类型变更联动-2026-09-29.md）。

    背景：原先联动只在 publish / restore 两处各算一次，`update_notice` 改
    `notice_type` / `effective_at` / `document_ids` 都不重算，实测出四种不一致：
      ① 改生效日   → 资料停在旧日期
      ② 新增关联资料 → 新资料不被截断（通知自称废止、资料却有效）
      ③ 移除关联资料 → 旧资料不回滚（永久停在废止日）
      ④ 改类型（废止→发布）→ 截断留着，且取消也不回滚（门禁依赖当前类型）

    本函数把「联动」重新定义为**由当前状态推导的一次重算**，而不是一次性动作：

        期望截断 = (status == "published") and (notice_type in AUTO_EXPIRE_TYPES)
        期望日期 = (effective_at or now()).date()

    `notice.auto_expired_docs` 的语义随之收敛为「**本通知当前仍持有的截断**」：

    - `prev[doc_id]` = 首次被本通知截断**之前**的值（**只在首次记账，永不覆盖**，
      所以反复编辑不会丢掉"原始失效日"）
    - `applied` = 本通知当前写入的失效日
    - 仍归本通知持有（`doc.effective_to == written`）时：可跟着新生效日走，可归还
    - **已不归本通知持有**（资料被管理员独立改过，或已被物理删除）时：条目**保留为
      "墓碑"**、本通知此后**永久不再干预它**。这一条是必须的 —— 若把条目删掉，下一次
      编辑该通知会被当成"首次关联"而重新截断，等于把管理员的独立修改悄悄冲掉。
    - 成功归还（回滚）后条目才移除；此时若该资料再次被关联，按"首次关联"处理

    安全护栏（防止误改别人写的数据）：

    - 回滚或改日期**仅当** `doc.effective_to` 仍等于本通知写过的值（`written`）；
      期间被管理员独立改过的资料一律不碰，并直接出账（不再持有）。
    - 首次截断沿用 publish 原规则：资料已在目标日或更早失效 → 跳过且不记账。
    - R1 冲突（effective_from > 目标日）抛异常 → 调用方事务整体回滚，绝不半改。
    - v1 旧账本（裸 {doc_id: 旧值}）无写入日，`written` 退回有效日/当天口径，
      与 _decode_auto_expired 的既有兜底一致，存量通知行为不变。
    """
    applied, prev_map = _decode_auto_expired(notice.auto_expired_docs)
    want_expire = (
        notice.status == "published" and notice.notice_type in AUTO_EXPIRE_TYPES
    )
    want_date = (notice.effective_at or datetime.now()).date()
    written = applied or want_date
    linked = {d.id: d for d in notice.documents}
    recorded = {int(k) for k in prev_map if k.isdigit()}

    def _restore(doc: Document, target: Optional[str], why: str) -> None:
        """回滚单条资料的时效，并留痕（与既有通过审计口径一致）。"""
        old = doc.effective_to.isoformat() if doc.effective_to else None
        restored = date.fromisoformat(target) if target else None
        doc.effective_to = restored
        audit_service.log(
            db,
            action=audit_service.ACTION_MODIFY_VALIDITY,
            user=current_user,
            object_type="document",
            object_id=doc.id,
            object_label=doc.title,
            detail={
                "via": why,
                "notice_id": notice.id,
                "notice_type": notice.notice_type,
                "reason": reason,
                "old": {"effective_to": old},
                "new": {"effective_to": doc.effective_to.isoformat() if doc.effective_to else None},
            },
            ip=ip,
            user_agent=user_agent,
        )

    # ---- ① 曾归本通知持有、现已解除关联 → 回滚并出账 ----
    for doc_id in sorted(recorded - set(linked)):
        key = str(doc_id)
        target = prev_map.pop(key, None)
        doc = db.get(Document, doc_id)
        if doc is None:
            continue  # 资料已物理删除，账本条目随之作废
        if doc.effective_to == written:  # 仍是本通知写的值才动
            _restore(doc, target, "notice_reconcile_unlink")

    def _truncate(doc: Document, new_date: date) -> None:
        """截断单条资料的时效，并留痕。"""
        old = doc.effective_to.isoformat() if doc.effective_to else None
        doc.effective_to = new_date
        audit_service.log(
            db,
            action=audit_service.ACTION_MODIFY_VALIDITY,
            user=current_user,
            object_type="document",
            object_id=doc.id,
            object_label=doc.title,
            detail={
                "via": via,
                "notice_id": notice.id,
                "notice_type": notice.notice_type,
                "reason": reason,
                "old": {"effective_to": old},
                "new": {"effective_to": new_date.isoformat()},
            },
            ip=ip,
            user_agent=user_agent,
        )

    # ---- ② 当前关联的资料：按期望状态对齐 ----
    # 两条互斥路径。「持有过」与「从未持有」必须分开 —— 混在一起会出这个 bug：
    # 资料被管理员独立改过 → 我们释放它 → 又落进"首次关联"分支被重新认领并覆盖
    # （2026-09-29 实测：doc 被独立改成 2027-06-30，改通知生效日后被冲成 2026-12-15）。
    for doc_id, doc in linked.items():
        key = str(doc_id)

        if key in prev_map:
            # ========== 路径一：本通知曾经/仍然截断过它 ==========
            if doc.effective_to != written:
                # 期间被独立改过（或账本来自 v1 旧口径）→ 已被别人接管。
                # 条目**留作墓碑**、此后永久不干预；删掉条目会导致下次编辑重新认领。
                continue
            if not want_expire:
                # 不再该截断（类型改了 / 已取消 / 未发布）→ 还回去
                _restore(doc, prev_map.pop(key), "notice_reconcile_release")
                continue
            if doc.effective_to == want_date:
                continue  # 已对齐
            validity_service.check_validity_r1(doc.effective_from, want_date)  # 冲突 → 整体回滚
            _truncate(doc, want_date)  # prev 保持首次记账的值，不被覆盖
            continue

        # ========== 路径二：从未截断过它（首次关联）==========
        if skip_archived and doc.archived_at is not None:
            continue  # restore 场景：已归档资料不重新截断
        if not want_expire:
            continue  # 本通知不产生截断效果，没有我们的事
        if doc.effective_to == want_date:
            continue  # 恰好已等于目标日 → 与 publish 同口径，不记账
        if doc.effective_to is not None and doc.effective_to <= want_date:
            continue  # publish 原规则：已在目标日或更早失效 → 跳过、不记账
        validity_service.check_validity_r1(doc.effective_from, want_date)  # 冲突 → 整体回滚
        prev_map[key] = doc.effective_to.isoformat() if doc.effective_to else None  # 首次记账
        _truncate(doc, want_date)

    # ---- 落账 ----
    if prev_map:
        # v2 信封：prev 供回滚，applied 供精确比对（取消时不再用 now() 现算）。
        # 旧格式（裸 {doc_id: 旧值}）仍可读，见 _decode_auto_expired。
        notice.auto_expired_docs = json.dumps(
            {
                "v": 2,
                "applied": (want_date if want_expire else written).isoformat(),
                "prev": prev_map,
            },
            ensure_ascii=False,
        )
    else:
        notice.auto_expired_docs = None



def _decode_auto_expired(
    raw: Optional[str],
) -> tuple[Optional[date], dict[str, Optional[str]]]:
    """解析 notice.auto_expired_docs → (本次实际写入的失效日, {资料id: 原失效日})。

    - **v2**（2026-09-29 起）：``{"v":2,"applied":"YYYY-MM-DD","prev":{...}}``
    - **v1**（存量数据）：裸 ``{"<doc_id>": "<原失效日>"}``。此格式没有记录写入日，
      applied 返回 None，调用方按旧口径（notice.effective_at 或当前日期）兜底 ——
      保证升级前后行为不突变，存量通知仍能按原逻辑回滚。
    """
    try:
        data = json.loads(raw or "")
    except (TypeError, ValueError):
        return None, {}
    if not isinstance(data, dict):
        return None, {}
    if data.get("v") == 2:
        applied: Optional[date] = None
        try:
            applied = date.fromisoformat(str(data.get("applied")))
        except (TypeError, ValueError):
            applied = None
        prev = data.get("prev")
        if not isinstance(prev, dict):
            return applied, {}
        return applied, {str(k): (str(v) if v else None) for k, v in prev.items()}
    # v1：整个 dict 即 prev_map
    return None, {str(k): (str(v) if v else None) for k, v in data.items()}
