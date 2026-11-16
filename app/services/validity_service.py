"""时效服务（docs/DESIGN.md §7.1–§7.3、项目规范 §13–§16、API.md §0.4）。

核心是**纯函数**动态计算，状态不存库（§14）：
    compute_status(...) → pending | active | expired | archived

规则（§14，日期闭区间）：
    as_of < effective_from                     → pending（待生效）
    effective_from <= as_of（且 effective_to 空或 as_of <= effective_to）→ active（有效）
    as_of > effective_to                       → expired（已失效）
    archived                                     → archived（已归档，优先级最高）

冲突检查（§16）：
    R1：effective_from > effective_to → 阻止保存（VALIDITY_CONFLICT）
    R2：修改既有资料时效窗口时，若新区间与旧区间重叠/包含，必须显式 confirm_overwrite，
        否则 409 阻止，禁止静默覆盖。
"""
from __future__ import annotations

from datetime import date as date_type
from typing import Optional

from app.core.errors import AppError

# 状态常量
STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"
STATUS_ARCHIVED = "archived"
ALL_STATUSES = (STATUS_PENDING, STATUS_ACTIVE, STATUS_EXPIRED, STATUS_ARCHIVED)


def compute_status(
    effective_from: Optional[date_type],
    effective_to: Optional[date_type],
    as_of_date: date_type,
    archived: bool = False,
) -> str:
    """按指定日期计算资料时效状态（§14、§15）。

    - effective_from 为 None 视为无生效日（防御性：模型层必填）
    - effective_to 为 None 表示"暂无失效日期"（不表示永久有效，§13）
    - 闭区间：as_of == effective_to 当天仍然有效
    """
    if archived:
        return STATUS_ARCHIVED
    if effective_from is None or as_of_date < effective_from:
        return STATUS_PENDING
    if effective_to is None or as_of_date <= effective_to:
        return STATUS_ACTIVE
    return STATUS_EXPIRED


def check_validity_r1(effective_from: date_type, effective_to: Optional[date_type]) -> None:
    """R1：生效日必填且生效日不得晚于失效日（§16）。违者 409 VALIDITY_CONFLICT。"""
    if effective_from is None:
        raise AppError("VALIDITY_CONFLICT", "生效日期（effective_from）必填")
    if effective_to is not None and effective_from > effective_to:
        raise AppError(
            "VALIDITY_CONFLICT",
            "生效日期倒挂：effective_from 晚于 effective_to（请检查日期范围）",
            detail={
                "effective_from": effective_from.isoformat(),
                "effective_to": effective_to.isoformat(),
            },
        )


def _as_infinity(value: Optional[date_type], *, lower: bool) -> date_type:
    """窗口比较用边界转换：None 视为 -inf（下限）或 +inf（上限）。"""
    if value is None:
        return date_type.min if lower else date_type.max
    return value


def windows_overlap(
    new_from: date_type,
    new_to: Optional[date_type],
    old_from: date_type,
    old_to: Optional[date_type],
) -> bool:
    """两个闭区间窗口是否重叠/包含（None 视为无界）。"""
    nf, nt = _as_infinity(new_from, lower=True), _as_infinity(new_to, lower=False)
    of, ot = _as_infinity(old_from, lower=True), _as_infinity(old_to, lower=False)
    return nf <= ot and of <= nt


def require_confirm_overwrite(
    *,
    old_from: date_type,
    old_to: Optional[date_type],
    new_from: date_type,
    new_to: Optional[date_type],
    confirm_overwrite: bool,
) -> None:
    """R2：既有资料窗口被重叠修改时，必须显式 confirm_overwrite=true，否则 409 阻止。

    - 传 confirm_overwrite 后由调用方负责写 modify_validity 审计（§16 R2）。
    """
    if windows_overlap(new_from, new_to, old_from, old_to) and not confirm_overwrite:
        raise AppError(
            "VALIDITY_CONFLICT",
            "新的有效期与既有有效期重叠，修改会覆盖现有时效设置；"
            "如确认覆盖请传 confirm_overwrite=true 并填写修改原因（modify_reason）",
            detail={
                "old": {"effective_from": old_from.isoformat(),
                        "effective_to": old_to.isoformat() if old_to else None},
                "new": {"effective_from": new_from.isoformat(),
                        "effective_to": new_to.isoformat() if new_to else None},
            },
        )