"""磁盘健康巡检（项目规范 §28 配套：本地备份方案下的"磁盘健康纳入日常巡检"）。

背景
----
项目规范 §28 锁定决策：**不做异地/双机/离机灾备拷贝**，应用内自动备份 +
同机保留策略即为最终方案；配套纪律是"服务器磁盘健康纳入日常巡检"。
但此前该纪律**没有任何自动化支撑**——磁盘写满时，表现为备份静默失败、
数据库写入报错，严重时数据损坏且无人察觉。

本模块提供三处落点：
1. `check_disk_health()` —— 返回可判定的健康快照（总量/可用量/阈值状态）；
2. `assert_disk_headroom()` —— 备份**执行前**的闸门：可用空间不足则拒绝备份
   （写一半的备份比没有备份更危险：会占满剩余空间）；
3. `DiskHealthGuard` —— 启动时巡检，低于阈值时告警（不阻止启动——磁盘紧张
   不等于不能服务，已有数据仍应可读可下载）。

阈值可配置（`DISK_MIN_FREE_MB` / `DISK_MIN_FREE_PERCENT`），默认 512MB / 5%。
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiskHealth:
    """磁盘健康快照。"""

    path: Path
    total_bytes: int
    free_bytes: int
    used_bytes: int
    free_percent: float
    min_free_bytes: int
    min_free_percent: float

    @property
    def ok(self) -> bool:
        """可用空间同时满足"绝对下限"与"相对下限"才算健康。

        两个阈值都要满足：只按绝对值会在超大盘上放过问题，只按百分比
        会在小盘上误报。
        """
        return (
            self.free_bytes >= self.min_free_bytes
            and self.free_percent >= self.min_free_percent
        )

    @property
    def free_mb(self) -> float:
        return self.free_bytes / (1024 * 1024)

    @property
    def total_mb(self) -> float:
        return self.total_bytes / (1024 * 1024)

    def describe(self) -> str:
        status = "正常" if self.ok else "不足"
        return (
            f"磁盘 {status}：{self.path} 可用 {self.free_mb:.0f}MB / 总 {self.total_mb:.0f}MB "
            f"({self.free_percent:.1f}%)，阈值 {self.min_free_bytes / (1024 * 1024):.0f}MB "
            f"与 {self.min_free_percent:.1f}%"
        )


def _resolve_target(path: Optional[Path] = None) -> Path:
    """巡检目标：默认 DATA_DIR 所在磁盘。

    用 DATA_DIR 而非备份目录，因为业务文件与数据库才是占用主体，且
    DATA_DIR 与 backups/ 同盘。
    """
    if path is not None:
        return path
    settings = get_settings()
    return settings.data_dir


def check_disk_health(
    path: Optional[Path] = None,
    *,
    min_free_mb: Optional[int] = None,
    min_free_percent: Optional[float] = None,
) -> DiskHealth:
    """采集磁盘健康快照。

    `path` 已给出时**不调用 get_settings()**（2026-10-29）：Settings 的
    get_settings() 会执行 ensure_dirs() 创建 6 个目录，而只读的部署前预检
    （scripts/check_env.py）也会调用本函数——那样它就不再是"只读"了。
    阈值需由调用方在只读场景下显式传入（见 min_free_* 参数）。
    """
    target = _resolve_target(path)
    if min_free_mb is None or min_free_percent is None:
        settings = get_settings()
        if min_free_mb is None:
            min_free_mb = settings.DISK_MIN_FREE_MB
        if min_free_percent is None:
            min_free_percent = settings.DISK_MIN_FREE_PERCENT
    usage = shutil.disk_usage(target)
    free_percent = (usage.free / usage.total * 100) if usage.total else 0.0
    return DiskHealth(
        path=target,
        total_bytes=usage.total,
        free_bytes=usage.free,
        used_bytes=usage.used,
        free_percent=free_percent,
        min_free_bytes=int(min_free_mb) * 1024 * 1024,
        min_free_percent=float(min_free_percent),
    )


def assert_disk_headroom(path: Optional[Path] = None) -> DiskHealth:
    """备份前的磁盘闸门：可用空间不足则抛 DiskSpaceError。

    为什么必须在备份**前**拦：备份是"写放大"操作——一个全量备份会复制
    全部业务文件。若此时空间已不足，备份写到一半就会失败，留下**半个备份
    目录**（既占空间，又不是可用的恢复点），把"快满了"变成"彻底满了"。
    """
    health = check_disk_health(path)
    if not health.ok:
        raise DiskSpaceError(health)
    return health


class DiskSpaceError(RuntimeError):
    """磁盘可用空间不足，无法安全执行写操作。"""

    def __init__(self, health: DiskHealth) -> None:
        super().__init__(f"磁盘空间不足：{health.describe()}")
        self.health = health


def log_startup_disk_health() -> DiskHealth:
    """启动时巡检：不足则打 ERROR 日志，但不阻止启动。

    不阻止启动的理由：磁盘紧张时系统仍应可访问（查看、下载、导出），
    贸然拒绝启动会让运维在半夜无法自救（甚至无法打开管理页做清理）。
    真正的硬闸门在备份入口（assert_disk_headroom）。
    """
    health = check_disk_health()
    if health.ok:
        logger.info("磁盘巡检：%s", health.describe())
    else:
        logger.error(
            "磁盘巡检：可用空间不足 —— %s；备份将自动跳过，"
            "请尽快清理磁盘或调整 DATA_DIR（项目规范 §28 磁盘健康巡检纪律）",
            health.describe(),
        )
    return health
