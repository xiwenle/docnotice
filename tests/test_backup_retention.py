"""备份保留策略 + 自动备份调度测试（确认单 2026-08-30）。

覆盖：天数清理 / 份数清理 / 关闭语义 / 目录名白名单 / pre_ 留旧组独立计数 /
source 来源记录 / 调度解析与下次触发时间 / 工作台报警阈值随调度变化。
"""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.scheduler import next_run, parse_schedule
from app.models.audit import AuditLog
from app.models.backup import BackupRecord

settings = get_settings()


def _create_backup(client, admin_headers, note=None) -> dict:
    """走 API 创建一次"仅数据库"备份（轻量、自动触发保留清理）。"""
    resp = client.post(
        "/api/v1/backups",
        json={"type": "database"},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _list_items(client, admin_headers) -> list[dict]:
    resp = client.get("/api/v1/backups", headers=admin_headers)
    assert resp.status_code == 200
    return resp.json()["items"]


def _make_pre_group(ts: str, data_dir, database_path) -> None:
    """造一组恢复留旧产物（业务目录 + 旧库快照 + WAL/SHM sidecar）。

    命名与 restore 真实落名一致：app.db → app.pre_<ts>（db_path.stem）。
    """
    d = data_dir / f"documents.pre_{ts}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "placeholder.txt").write_text("pre", encoding="utf-8")
    base = database_path.parent / f"{database_path.stem}.pre_{ts}"
    base.write_text("old-db", encoding="utf-8")
    Path(f"{base}-wal").write_text("old-wal", encoding="utf-8")
    Path(f"{base}-shm").write_text("old-shm", encoding="utf-8")


class TestRetentionByDays:
    def test_expired_backup_purged(self, client, admin_headers, db, monkeypatch):
        monkeypatch.setattr(settings, "BACKUP_KEEP_DAYS", 90)
        old = _create_backup(client, admin_headers)
        db.execute(
            update(BackupRecord)
            .where(BackupRecord.id == old["id"])
            .values(created_at=datetime.now() - timedelta(days=91))
        )
        db.commit()

        _create_backup(client, admin_headers)  # 触发保留清理

        items = {i["id"]: i for i in _list_items(client, admin_headers)}
        assert old["id"] not in items, "超期备份记录应被真删除（列表不再出现）"

    def test_fresh_backup_survives(self, client, admin_headers):
        fresh = _create_backup(client, admin_headers)
        items = {i["id"]: i for i in _list_items(client, admin_headers)}
        assert items[fresh["id"]]["status"] == "completed"
        # 清理后目录仍在
        assert (settings.data_dir / fresh["backup_path"]).exists()


class TestRetentionByCount:
    def test_keep_newest_n(self, client, admin_headers, monkeypatch):
        monkeypatch.setattr(settings, "BACKUP_KEEP_DAYS", 0)  # 关闭天数规则
        monkeypatch.setattr(settings, "BACKUP_KEEP_COUNT", 3)
        created = [_create_backup(client, admin_headers) for _ in range(5)]
        ids = [c["id"] for c in created]
        paths = {c["id"]: c["backup_path"] for c in created}

        items = {i["id"]: i for i in _list_items(client, admin_headers)}
        survivors = [i for i in items.values() if i["status"] == "completed"]
        assert len(survivors) == 3
        # 保留的是最新 3 份
        assert {i["id"] for i in survivors} == set(ids[-3:])
        for victim_id in ids[:2]:
            assert victim_id not in items, "超量备份记录应被真删除"

        # 本测试创建的目录中，留存者必须与存活记录一致（ victims 或已删除、
        # 或名字被后续备份复用；会话级 data_dir 有更早测试的目录残留，
        # 因此只对本测试创建的集合断言）
        created_names = {paths[v].split("/")[-1] for v in ids}
        dirs_now = {
            p.name for p in (settings.data_dir / "backups").glob("backup_*") if p.is_dir()
        }
        survivor_names = {i["backup_path"].split("/")[-1] for i in survivors}
        assert dirs_now & created_names == survivor_names, (
            f"磁盘与记录不一致: {dirs_now & created_names} vs {survivor_names}"
        )


class TestRetentionDisabled:
    def test_zero_means_disabled(self, client, admin_headers, db, monkeypatch):
        monkeypatch.setattr(settings, "BACKUP_KEEP_DAYS", 0)
        monkeypatch.setattr(settings, "BACKUP_KEEP_COUNT", 0)
        old = _create_backup(client, admin_headers)
        db.execute(
            update(BackupRecord)
            .where(BackupRecord.id == old["id"])
            .values(created_at=datetime.now() - timedelta(days=999))
        )
        db.commit()
        another = _create_backup(client, admin_headers)

        items = {i["id"]: i for i in _list_items(client, admin_headers)}
        assert items[old["id"]]["status"] == "completed"
        assert items[another["id"]]["status"] == "completed"


class TestRetentionWhitelist:
    def test_non_matching_paths_untouched(
        self, client, admin_headers, monkeypatch
    ):
        monkeypatch.setattr(settings, "BACKUP_KEEP_DAYS", 0)
        monkeypatch.setattr(settings, "BACKUP_KEEP_COUNT", 0)
        junk = settings.data_dir / "backups" / "backup_junk"
        stage = settings.data_dir / "backups" / "restore_stage_tmp"
        orphan = settings.data_dir / "backups" / "backup_20991231_235959"
        for d in (junk, stage, orphan):
            d.mkdir(parents=True, exist_ok=True)
            (d / "x.txt").write_text("keep", encoding="utf-8")

        _create_backup(client, admin_headers)  # 触发清理

        for d in (junk, stage, orphan):
            assert (d / "x.txt").exists(), f"白名单外目录被误删: {d}"


class TestPreGroups:
    def test_keep_newest_groups_independent(
        self, client, admin_headers, monkeypatch
    ):
        # 会话级 data_dir 是共享的：其他测试（如恢复流程）可能已产生真实 pre_
        # 组且时间戳更新，会挤占"最近 3 组"名额——先清场保证断言确定性
        import shutil

        for p in settings.data_dir.glob("*.pre_*"):
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        for p in settings.database_path.parent.glob("app.pre_*"):
            p.unlink()

        monkeypatch.setattr(settings, "BACKUP_KEEP_DAYS", 0)
        monkeypatch.setattr(settings, "BACKUP_KEEP_COUNT", 0)  # 份数不挤占 pre 组
        for i, ts in enumerate(
            ["20260101_000000000001", "20260102_000000000002",
             "20260103_000000000003", "20260104_000000000004",
             "20260105_000000000005"]
        ):
            _make_pre_group(ts, settings.data_dir, settings.database_path)
        monkeypatch.setattr(settings, "BACKUP_PRE_KEEP_COUNT", 3)

        _create_backup(client, admin_headers)  # 触发清理

        kept = sorted(
            p.name for p in settings.data_dir.glob("documents.pre_*")
        )
        assert len(kept) == 3
        # 保留的是最新 3 组（字典序=时间序）
        assert kept == [
            "documents.pre_20260103_000000000003",
            "documents.pre_20260104_000000000004",
            "documents.pre_20260105_000000000005",
        ]
        # 同组旧库快照 + WAL/SHM sidecar 整组清理/保留（2026-10-21）
        db_parent = settings.database_path.parent
        for suffix in ("", "-wal", "-shm"):
            assert not (db_parent / f"app.pre_20260101_000000000001{suffix}").exists()
            assert (db_parent / f"app.pre_20260105_000000000005{suffix}").exists()


class TestSource:
    def test_manual_via_api(self, client, admin_headers):
        created = _create_backup(client, admin_headers)
        items = {i["id"]: i for i in _list_items(client, admin_headers)}
        assert items[created["id"]]["source"] == "manual"

    def test_auto_via_service(self, client, admin_headers, db, admin_user):
        from app.services import backup_service

        record = backup_service.create_backup(
            db, current_user=admin_user, backup_type="database",
            note="自动备份（调度）", source="auto",
        )
        items = {i["id"]: i for i in _list_items(client, admin_headers)}
        assert items[record.id]["source"] == "auto"

    def test_policy_in_list_response(self, client, admin_headers, monkeypatch):
        monkeypatch.setattr(settings, "BACKUP_SCHEDULE", "sat 03:00")
        resp = client.get("/api/v1/backups", headers=admin_headers)
        policy = resp.json()["policy"]
        assert policy["schedule"] == "sat 03:00"
        assert policy["keep_days"] == 90
        assert policy["pre_keep_count"] == 3

    def test_policy_disabled_schedule(self, client, admin_headers, monkeypatch):
        monkeypatch.setattr(settings, "BACKUP_SCHEDULE", "")
        resp = client.get("/api/v1/backups", headers=admin_headers)
        assert resp.json()["policy"]["schedule"] is None


class TestScheduleParsing:
    def test_parse_weekly(self):
        weekday, hour, minute, interval = parse_schedule("sat 03:00")
        assert weekday == 5 and hour == 3 and minute == 0 and interval == 7

    def test_parse_daily(self):
        weekday, hour, minute, interval = parse_schedule("daily 00:30")
        assert weekday is None and interval == 1

    @pytest.mark.parametrize("bad", ["monday 03:00", "sat 25:00", "sat", "sat 3:xx"])
    def test_parse_invalid(self, bad):
        with pytest.raises(ValueError):
            parse_schedule(bad)

    def test_next_run_weekly_from_middle_of_week(self):
        base = datetime(2026, 8, 5, 12, 0)  # 周三中午
        nr = next_run(base, "sat 03:00")
        assert nr.weekday() == 5  # 周六（Mon=0）
        assert nr > base
        assert nr.hour == 3 and nr.minute == 0
        assert (nr - base).days <= 7

    def test_next_run_daily_same_day_future(self):
        base = datetime(2026, 8, 5, 1, 0)
        assert next_run(base, "daily 03:00") == datetime(2026, 8, 5, 3, 0)

    def test_next_run_daily_past_time_rolls_to_tomorrow(self):
        base = datetime(2026, 8, 5, 3, 0)
        nr = next_run(base, "daily 03:00")
        assert nr == datetime(2026, 8, 6, 3, 0)


class TestDashboardStaleDays:
    def test_threshold_follows_schedule(self, monkeypatch):
        from app.services import backup_service

        monkeypatch.setattr(settings, "BACKUP_SCHEDULE", "sat 03:00")
        assert backup_service.backup_stale_days() == 8
        monkeypatch.setattr(settings, "BACKUP_SCHEDULE", "daily 03:00")
        assert backup_service.backup_stale_days() == 2
        monkeypatch.setattr(settings, "BACKUP_SCHEDULE", "")
        assert backup_service.backup_stale_days() == 7
        monkeypatch.setattr(settings, "BACKUP_SCHEDULE", "非法配置")
        assert backup_service.backup_stale_days() == 7

    def test_dashboard_api_exposes_threshold(self, client, admin_headers):
        from app.core.cache import cache

        cache.clear()
        resp = client.get("/api/v1/dashboard", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["backup_stale_days"] == 8


class TestOrphanBackupReconcile:
    """孤儿备份目录认领（2026-09-15 缺陷修复）。

    背景（实测复现）：恢复备份会覆盖整个 app.db，快照之后创建的备份**记录行
    随回滚消失、磁盘目录仍在** → 变成列表看不见、保留策略也管不到的孤儿。
    处置口径为「认领，不删除」（§28 宁多留不误删）。

    隔离说明：test_backup_retention 全类的 data_dir 是**会话级共享**的，同批其他
    用例（恢复流程/保留清理）也会在 backups/ 下留目录。因此这里：
    ① 断言一律针对本用例自己造的目录名，不假设「总数就我一个」；
    ② 每个用例用独立时间戳前缀，互不干扰。
    """

    # 本类每条用例自己造的目录名（_cleanup 精确清理，互不干扰；
    # 不能用单一日期前缀过滤——各用例日期不同，过滤会漏掉自己造的目录）
    _created: set[str] = set()

    @classmethod
    def _cleanup(cls):
        """清掉本类造过的目录，避免污染后续用例的断言。"""
        if settings.backups_dir.is_dir():
            for name in cls._created:
                shutil.rmtree(settings.backups_dir / name, ignore_errors=True)
        cls._created.clear()

    def _mk(
        self,
        name: str,
        *,
        manifest: str | None = None,
        appdb: bytes | None = b"x" * 64,
        with_files: bool = True,
    ):
        import json as _json

        d = settings.backups_dir / name
        (d / "files").mkdir(parents=True, exist_ok=True) if with_files else d.mkdir(parents=True, exist_ok=True)
        if appdb is not None:
            (d / "app.db").write_bytes(appdb)
        if manifest is None:
            manifest = _json.dumps(
                {"backup_type": "full", "db_sha256": "a" * 64, "file_count": 2}
            )
        if manifest:
            (d / "manifest.json").write_text(manifest, encoding="utf-8")
        self._created.add(name)
        return d

    def _mine(self, result: dict) -> list[str]:
        """只取本用例自己造的目录的认领结果（其余是同批用例留下的目录）。"""
        return sorted(
            r["path"] for r in result["reclaimed"]
            if Path(r["path"]).name in self._created
        )

    def test_reclaims_orphan_dir(self, db, admin_user):
        from app.services import backup_service

        self._cleanup()
        try:
            self._mk("backup_20260101_010101")
            res = backup_service.reconcile_orphan_backups(db, current_user=admin_user)

            assert self._mine(res) == ["backups/backup_20260101_010101"]
            row = db.execute(
                select(BackupRecord).where(
                    BackupRecord.backup_path == "backups/backup_20260101_010101"
                )
            ).scalar_one()
            assert row.status == "completed"
            assert row.backup_type == "full"
            assert row.file_count == 2
            assert "自动认领" in (row.note or "")
        finally:
            self._cleanup()

    def test_idempotent(self, db, admin_user):
        """已登记的目录不重复认领（启动迁移每次开机都会跑）。"""
        from app.services import backup_service

        self._cleanup()
        try:
            self._mk("backup_20260102_010101")
            first = backup_service.reconcile_orphan_backups(db, current_user=admin_user)
            second = backup_service.reconcile_orphan_backups(db, current_user=admin_user)
            assert self._mine(first) == ["backups/backup_20260102_010101"]
            assert self._mine(second) == []
            rows = db.execute(
                select(BackupRecord).where(
                    BackupRecord.backup_path == "backups/backup_20260102_010101"
                )
            ).scalars().all()
            assert len(rows) == 1
        finally:
            self._cleanup()

    def test_skips_unusable_and_unknown_dirs(self, db, admin_user):
        """只认领「可恢复」的目录：缺 app.db / manifest 损坏 / 白名单外一律不碰。"""
        from app.services import backup_service

        self._cleanup()
        try:
            self._mk("backup_20260103_010101")                    # 可用 → 认领
            self._mk("backup_20260103_020202", appdb=None)        # 无 app.db → 跳过
            self._mk("backup_20260103_030303", appdb=b"")         # 空 db → 跳过
            self._mk("backup_20260103_040404", manifest="{ 坏")   # manifest 损坏 → 跳过
            junk = settings.backups_dir / "backup_junk"           # 白名单外
            junk.mkdir(parents=True, exist_ok=True)
            stage = settings.backups_dir / "restore_stage_x"      # 白名单外
            stage.mkdir(parents=True, exist_ok=True)

            res = backup_service.reconcile_orphan_backups(db, current_user=admin_user)

            assert self._mine(res) == ["backups/backup_20260103_010101"]
            mine_skipped = {
                s["dir"] for s in res["skipped"] if s["dir"] in self._created
            }
            assert mine_skipped == {
                "backup_20260103_020202",
                "backup_20260103_030303",
                "backup_20260103_040404",
            }
            # 白名单外目录绝不被删除/改动
            assert junk.is_dir() and stage.is_dir()
        finally:
            self._cleanup()
            shutil.rmtree(junk, ignore_errors=True)
            shutil.rmtree(stage, ignore_errors=True)

    def _all_backups(self, client, admin_headers) -> list[dict]:
        """翻完所有页取全部备份（列表按 id desc 分页，page_size 上限 100）。

        批内前面用例会产生大量备份记录，只取第一页会漏掉本用例刚认领的记录
        （这正是本测试第一次写成"只看第一页"时的失败原因）。
        """
        out: list[dict] = []
        page = 1
        while True:
            resp = client.get(
                "/api/v1/backups",
                headers=admin_headers,
                params={"page": page, "page_size": 100},
            )
            assert resp.status_code == 200
            body = resp.json()
            out.extend(body["items"])
            if page >= body["pages"] or not body["items"]:
                return out
            page += 1

    def test_reclaimed_dir_is_listed_and_managed(self, client, admin_headers, db, admin_user):
        """认领后必须重新出现在备份列表（即回到保留策略管辖范围）。"""
        from app.services import backup_service

        self._cleanup()
        try:
            self._mk("backup_20260104_010101")
            listed = self._all_backups(client, admin_headers)
            assert not any(
                b["backup_path"] == "backups/backup_20260104_010101" for b in listed
            ), "认领前不应出现在列表"

            backup_service.reconcile_orphan_backups(db, current_user=admin_user)

            items = self._all_backups(client, admin_headers)
            hit = [b for b in items if b["backup_path"] == "backups/backup_20260104_010101"]
            assert len(hit) == 1
            assert hit[0]["status"] == "completed"
        finally:
            self._cleanup()

    def test_reconcile_writes_audit(self, client, admin_headers, db, admin_user):
        """认领动作必须留痕（可追溯，非静默改数据）。"""
        from app.services import backup_service

        self._cleanup()
        try:
            self._mk("backup_20260105_010101")
            backup_service.reconcile_orphan_backups(db, current_user=admin_user)

            logs = client.get(
                "/api/v1/audit-logs?action=backup_reconcile", headers=admin_headers
            ).json()
            assert logs["total"] >= 1
            assert any(
                "backup_20260105_010101" in (i["detail"] or "") for i in logs["items"]
            )
        finally:
            self._cleanup()

    def test_no_audit_when_nothing_to_reclaim(self, db, admin_user):
        """无孤儿时不写审计（避免每次开机刷一条噪声）。"""
        from app.services import backup_service

        self._cleanup()
        try:
            before = len(db.execute(select(AuditLog)).scalars().all())
            res = backup_service.reconcile_orphan_backups(db, current_user=admin_user)
            after = len(db.execute(select(AuditLog)).scalars().all())
            # 本用例没造任何目录 → 本类命名空间内零认领；
            # 同批其他用例留下的目录不归本用例断言（data_dir 会话级共享）
            assert self._mine(res) == []
            if after != before:
                # 只有「认领了别人的目录」才允许新增审计
                assert res["reclaimed"], "新增了审计却没有任何认领，属异常"
        finally:
            self._cleanup()

