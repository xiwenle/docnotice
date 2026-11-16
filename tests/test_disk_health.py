"""磁盘健康巡检与备份闸门（app/core/disk_health.py）测试。

覆盖：
- 健康快照的采集与两个阈值（绝对 MB / 相对 %）的联合判定
- assert_disk_headroom 在空间不足时抛 DiskSpaceError
- **备份被磁盘闸门拦下时，不得留下半个备份目录**（核心不变量）
- 调度线程遇到 AppError（磁盘不足）不崩溃

用 monkeypatch 伪造 shutil.disk_usage，不依赖真实磁盘余量。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.core import disk_health
from app.core.disk_health import (
    DiskSpaceError,
    assert_disk_headroom,
    check_disk_health,
    log_startup_disk_health,
)
from app.core.errors import AppError
from app.services import backup_service


def _fake_usage(total: int, free: int):
    return shutil._ntuple_diskusage(total, total - free, free)


@pytest.fixture
def fake_disk(monkeypatch):
    """伪造磁盘用量；返回一个可调整的容器。"""

    class _Box:
        total = 100 * 1024 * 1024 * 1024
        free = 50 * 1024 * 1024 * 1024

    box = _Box()

    def _usage(path):
        return _fake_usage(box.total, box.free)

    monkeypatch.setattr(disk_health.shutil, "disk_usage", _usage)
    return box


# ---------------------------------------------------------------------------
# 健康快照
# ---------------------------------------------------------------------------
class TestDiskHealthSnapshot:
    def test_reports_totals_and_free_space(self, fake_disk, tmp_path):
        fake_disk.total = 10 * 1024 * 1024 * 1024
        fake_disk.free = 1 * 1024 * 1024 * 1024
        health = check_disk_health(tmp_path)
        assert health.total_bytes == 10 * 1024 * 1024 * 1024
        assert health.free_bytes == 1 * 1024 * 1024 * 1024
        assert health.free_percent == pytest.approx(10.0, abs=0.01)

    def test_ok_when_both_thresholds_met(self, fake_disk, tmp_path):
        fake_disk.total = 10 * 1024 * 1024 * 1024
        fake_disk.free = 1 * 1024 * 1024 * 1024
        assert check_disk_health(tmp_path).ok is True

    def test_not_ok_when_free_below_absolute_threshold(self, fake_disk, tmp_path):
        """绝对阈值不满足 → 不健康（哪怕百分比看起来够）。"""
        fake_disk.total = 1000 * 1024 * 1024
        fake_disk.free = 100 * 1024 * 1024  # 10%，但 < 512MB 默认绝对阈值
        health = check_disk_health(tmp_path)
        assert health.free_percent > health.min_free_percent
        assert health.ok is False

    def test_not_ok_when_free_below_percent_threshold(self, fake_disk, tmp_path):
        """相对阈值不满足 → 不健康（哪怕绝对值还很大）。"""
        fake_disk.total = 100 * 1024 * 1024 * 1024
        fake_disk.free = 2 * 1024 * 1024 * 1024  # 2% < 5%
        health = check_disk_health(tmp_path)
        assert health.free_bytes > health.min_free_bytes
        assert health.ok is False

    def test_describe_mentions_status(self, fake_disk, tmp_path):
        assert "正常" in check_disk_health(tmp_path).describe()


# ---------------------------------------------------------------------------
# 备份闸门
# ---------------------------------------------------------------------------
class TestHeadroomGate:
    def test_passes_when_healthy(self, fake_disk, tmp_path):
        fake_disk.total = 10 * 1024 * 1024 * 1024
        fake_disk.free = 1 * 1024 * 1024 * 1024
        assert assert_disk_headroom(tmp_path).ok is True

    def test_raises_when_insufficient(self, fake_disk, tmp_path):
        fake_disk.total = 1000 * 1024 * 1024
        fake_disk.free = 10 * 1024 * 1024
        with pytest.raises(DiskSpaceError) as exc_info:
            assert_disk_headroom(tmp_path)
        assert exc_info.value.health.ok is False


# ---------------------------------------------------------------------------
# 备份被拦下时不得留下半个备份目录（核心不变量）
# ---------------------------------------------------------------------------
class TestBackupGuardedByDisk:
    def test_backup_rejected_when_disk_full(self, fake_disk, db, admin_user, tmp_path):
        """磁盘不足 → 备份被拒，且 backups/ 下不得新增任何目录。"""
        fake_disk.total = 1000 * 1024 * 1024
        fake_disk.free = 5 * 1024 * 1024  # 远低于阈值

        backups_root = Path(backup_service.settings.backups_dir)
        before = sorted(p.name for p in backups_root.iterdir()) if backups_root.exists() else []

        with pytest.raises(AppError) as exc_info:
            backup_service.create_backup(db, current_user=admin_user, backup_type="full")

        assert "磁盘" in exc_info.value.message

        after = sorted(p.name for p in backups_root.iterdir()) if backups_root.exists() else []
        assert after == before, "磁盘不足时不得留下半个备份目录"

    def test_backup_succeeds_when_disk_healthy(self, fake_disk, db, admin_user):
        """对照组：空间充足时备份正常创建。"""
        fake_disk.total = 10 * 1024 * 1024 * 1024
        fake_disk.free = 1 * 1024 * 1024 * 1024
        record = backup_service.create_backup(db, current_user=admin_user, backup_type="full")
        assert record.status == "completed"


# ---------------------------------------------------------------------------
# 启动巡检：只告警不抛
# ---------------------------------------------------------------------------
class TestStartupGuard:
    def test_startup_check_does_not_raise_when_full(self, fake_disk, tmp_path):
        """磁盘紧张时系统仍应可启动（否则运维半夜无法自救）。"""
        fake_disk.total = 1000 * 1024 * 1024
        fake_disk.free = 5 * 1024 * 1024
        health = log_startup_disk_health()
        assert health.ok is False

    def test_startup_check_ok_when_healthy(self, fake_disk, tmp_path):
        fake_disk.total = 10 * 1024 * 1024 * 1024
        fake_disk.free = 1 * 1024 * 1024 * 1024
        assert log_startup_disk_health().ok is True


# ---------------------------------------------------------------------------
# 调度线程：AppError 不导致异常外泄
# ---------------------------------------------------------------------------
class TestSchedulerResilience:
    def test_scheduler_survives_disk_error(self, fake_disk, monkeypatch, db, admin_user):
        """磁盘不足导致 AppError 时，自动备份记错误日志但不抛出。"""
        fake_disk.total = 1000 * 1024 * 1024
        fake_disk.free = 5 * 1024 * 1024

        from app.core import scheduler

        # 注入一个必然抛 AppError 的 create_backup
        def _boom(*args, **kwargs):
            raise AppError("VALIDATION_ERROR", "磁盘可用空间不足，已拒绝创建备份")

        monkeypatch.setattr(backup_service, "create_backup", _boom)
        # 不应抛出异常
        scheduler._run_scheduled_backup()
