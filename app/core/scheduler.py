"""应用内自动备份调度（纯标准库线程，不依赖 cron/systemd；AGENTS §6/§29）。

- BACKUP_SCHEDULE："sat 03:00"（每周六 03:00）/ "daily 03:00"（每天）；留空=关闭
- 每次触发：以系统中最早启用的管理员名义执行全量备份（source=auto）
  + 保留策略清理；与手动备份同一条代码路径与审计
- 启动自愈：若距上次成功备份超过一个调度周期（或从未备份），立即补跑一次
- 单次失败不终止调度：记审计与 error 日志，等待下个周期
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

from app.core.config import get_settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)

settings = get_settings()

_WEEKDAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_next_run_at: Optional[datetime] = None  # 当前计划的下一次触发时间（API 展示用）


def parse_schedule(text: str) -> tuple[Optional[int], int, int, int]:
    """解析调度串 → (星期几或 None=daily, 时, 分, 周期天数)；非法抛 ValueError。"""
    parts = (text or "").strip().lower().split()
    if len(parts) != 2:
        raise ValueError(f"调度格式应为 'sat 03:00' 或 'daily 03:00'，实际: {text!r}")
    day = parts[0]
    if day != "daily" and day not in _WEEKDAY_MAP:
        raise ValueError(f"未知星期: {day!r}")
    hh, mm = parts[1].split(":")
    hour, minute = int(hh), int(mm)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"时间非法: {parts[1]!r}")
    weekday = None if day == "daily" else _WEEKDAY_MAP[day]
    interval_days = 1 if weekday is None else 7
    return weekday, hour, minute, interval_days


def next_run(now: datetime, schedule: str) -> datetime:
    """给定基准时间，计算下一次触发时间（严格晚于 now）。"""
    weekday, hour, minute, _ = parse_schedule(schedule)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    if weekday is not None:
        while candidate.weekday() != weekday:
            candidate += timedelta(days=1)
    return candidate


def current_next_run() -> Optional[datetime]:
    """当前计划的下一次触发时间（未启动/未启用时为 None）。"""
    return _next_run_at


def _operator_id(db) -> Optional[int]:
    """自动备份的操作人：系统中最早启用的管理员（无则跳过本次）。"""
    from sqlalchemy import select

    from app.models.user import User

    return db.execute(
        select(User.id)
        .where(User.role == User.ROLE_ADMIN, User.is_active.is_(True))
        .order_by(User.id.asc())
        .limit(1)
    ).scalar_one_or_none()


def _run_scheduled_backup() -> None:
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.services import backup_service

    db = SessionLocal()
    try:
        op_id = _operator_id(db)
        if op_id is None:
            logger.error("自动备份跳过：系统中没有启用的管理员账号")
            return
        operator = db.get(User, op_id)
        if operator is None:
            logger.error("自动备份跳过：管理员账号不存在")
            return
        record = backup_service.create_backup(
            db,
            current_user=operator,
            backup_type="full",
            note="自动备份（调度）",
            source="auto",
        )
        summary = backup_service.apply_retention(db, current_user=operator)
        logger.info(
            "自动备份完成 id=%s；保留清理：%s", record.id, summary
        )
    except AppError as exc:
        # 业务级拒绝（如磁盘空间不足）：不是崩溃，记清楚原因等下个周期
        logger.error("自动备份未执行：%s（%s）", exc.code, exc.message)
    except Exception:  # noqa: BLE001
        logger.exception("自动备份执行失败（等待下个周期）")
    finally:
        db.close()


def _catch_up_needed() -> bool:
    """距上次成功备份超过一个调度周期（或从未备份）→ 需要补跑。"""
    from sqlalchemy import func, select

    from app.db.session import SessionLocal
    from app.models.backup import BackupRecord

    db = SessionLocal()
    try:
        last = db.execute(
            select(func.max(BackupRecord.created_at)).where(
                BackupRecord.status.in_(("completed", "restored"))
            )
        ).scalar_one_or_none()
    finally:
        db.close()
    if last is None:
        return True
    interval_days = parse_schedule(settings.BACKUP_SCHEDULE)[3]
    return datetime.now() - last > timedelta(days=interval_days)


def _loop() -> None:
    global _next_run_at
    schedule = settings.BACKUP_SCHEDULE.strip()
    while not _stop.is_set():
        _next_run_at = next_run(datetime.now(), schedule)
        while True:
            if _stop.wait(20):
                return
            if datetime.now() >= _next_run_at:
                break
        _next_run_at = None
        _run_scheduled_backup()


def start() -> None:
    """启动调度线程（lifespan 调用；未配置/配置非法/test 环境不启动）。"""
    global _thread
    schedule = (settings.BACKUP_SCHEDULE or "").strip()
    if not schedule:
        logger.info("自动备份未启用（BACKUP_SCHEDULE 为空）")
        return
    if settings.APP_ENV == "test":
        return
    try:
        parse_schedule(schedule)
    except ValueError as exc:
        logger.error("自动备份未启用：BACKUP_SCHEDULE 配置非法（%s）", exc)
        return

    def _runner() -> None:
        if _catch_up_needed():
            logger.info("距上次成功备份超过一个调度周期，启动补跑一次")
            _run_scheduled_backup()
        _loop()

    _stop.clear()
    _thread = threading.Thread(target=_runner, name="backup-scheduler", daemon=True)
    _thread.start()
    logger.info("自动备份调度已启动: %s", schedule)


def stop() -> None:
    """停止调度线程（进程退出时调用；线程为 daemon，不阻塞退出）。"""
    _stop.set()
