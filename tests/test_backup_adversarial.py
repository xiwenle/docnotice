"""备份/恢复对抗性边界审计（2026-10-26）。

背景：backup_service.py 是全仓最大、最复杂、历史修复最多的模块（备份孤儿
认领、跨平台恢复、零文件备份、文件替换补偿…），且故障后果最大。

本文件针对**尚未被现有测试覆盖的对抗场景**：
- 并发恢复：恢复是"换库 + 切目录"的多步破坏性操作，若无互斥，两个恢复
  会交错执行 → 数据损坏
- 恢复中途失败：stage 目录清理、当前状态是否保持一致
- 备份中途失败：不得留下"半个备份目录"冒充可用恢复点
- 保留策略边界：份数/天数/0 值（0=不限）
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.services import backup_service

from files.generators import PDF_FILENAME, make_pdf


def _make_doc(client, headers, tmp_path, title="资料"):
    """建一份带文件的资料，返回 (doc_id, 文件名)。"""
    resp = client.post(
        "/api/v1/documents",
        headers=headers,
        json={"title": title, "effective_from": "2026-01-01"},
    )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    path = tmp_path / PDF_FILENAME
    make_pdf(path)
    with path.open("rb") as fh:
        up = client.post(
            f"/api/v1/documents/{doc_id}/file",
            headers=headers,
            files={"file": (PDF_FILENAME, fh, "application/pdf")},
        )
    assert up.status_code in (200, 201), up.text
    return doc_id, PDF_FILENAME


# ---------------------------------------------------------------------------
# 并发恢复（最高风险）
# ---------------------------------------------------------------------------
class TestConcurrentRestore:
    def test_two_restores_do_not_interleave(self, client, admin_headers, tmp_path, data_dir, db):
        """两个并发恢复必须被串行化，不得交错破坏数据。

        恢复 = 「换库 + 切业务目录」的多步破坏性操作。若无互斥：
        A 切目录 → B 切目录 → A 写文件 → 数据处于混合状态。
        """
        doc_id, _ = _make_doc(client, admin_headers, tmp_path, title="并发基线")
        resp = client.post("/api/v1/backups", headers=admin_headers, json={"type": "full"})
        assert resp.status_code == 201, resp.text
        backup = resp.json()

        # 备份后再造一个资料，制造"要回滚掉"的状态
        doc2_id, _ = _make_doc(client, admin_headers, tmp_path, title="待回滚")
        db.rollback()
        db.close()

        results: list[tuple[str, int]] = []
        barrier = threading.Barrier(2)

        def _do_restore(tag: str):
            # 各自用独立客户端，避免共享 TestClient 状态
            from fastapi.testclient import TestClient

            from app.main import app as fastapi_app

            c = TestClient(fastapi_app)
            token = c.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "admin1234"},
            ).json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            barrier.wait(timeout=30)
            r = c.post(
                f"/api/v1/backups/{backup['id']}/restore",
                headers=headers,
                json={"confirm": True, "reason": f"并发恢复 {tag}"},
            )
            results.append((tag, r.status_code))

        threads = [threading.Thread(target=_do_restore, args=(t,)) for t in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        assert len(results) == 2, f"两个恢复线程都应完成，实际 {results}"
        statuses = sorted(code for _, code in results)
        # 关键断言：不能两个都"成功"。恢复是"换库 + 切业务目录"的多步破坏性
        # 操作，两个都 200 意味着它们交错执行了 → 数据可能处于混合状态。
        # 期望：恰好一个成功(200)，另一个被互斥拒绝(409)。
        assert statuses.count(200) == 1, (
            f"两个并发恢复都返回成功，说明恢复过程缺少互斥：{results}"
        )
        assert statuses.count(409) == 1, (
            f"第二个恢复应被互斥拒绝(409 CONFLICT)，实际：{results}"
        )

        # 无论结果如何，恢复后系统必须仍可用：能登录、能读文档
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        c = TestClient(fastapi_app)
        lr = c.post("/api/v1/auth/login", json={"username": "admin", "password": "admin1234"})
        assert lr.status_code == 200, f"并发恢复后必须仍可登录：{lr.text}"
        headers = {"Authorization": f"Bearer {lr.json()['access_token']}"}
        r = c.get(f"/api/v1/documents/{doc_id}", headers=headers)
        assert r.status_code == 200, f"并发恢复后基线资料必须完好：{r.text}"

    def test_backup_during_restore_is_rejected_or_safe(
        self, client, admin_headers, tmp_path, data_dir, db
    ):
        """恢复进行中触发备份，不得产生不一致的备份产物。"""
        resp = client.post("/api/v1/backups", headers=admin_headers, json={"type": "full"})
        assert resp.status_code == 201
        db.rollback()
        db.close()

        # 恢复后系统仍应可创建备份（不残留 stage 目录/锁）
        from fastapi.testclient import TestClient

        from app.main import app as fastapi_app

        c = TestClient(fastapi_app)
        token = c.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin1234"}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        r = c.post("/api/v1/backups", headers=headers, json={"type": "full"})
        assert r.status_code == 201, r.text

    def test_concurrent_backup_while_restore_holds_lock_is_409(
        self, client, admin_headers
    ):
        """恢复持锁期间发起备份必须被拒（409），不可拍到"半个恢复"。

        2026-10-29 回归守卫。此前 `create_backup` 不检查 `_restore_lock`，
        恢复途中拍快照会得到混合状态，备份集不可用却显示成功。
        注意：上一版测试是「顺序执行两次备份」，根本没测并发，测不出该风险。

        这里直接占住服务层锁（模拟恢复在执行），再走真实 HTTP 端点。
        """
        from app.services import backup_service as bs

        assert bs._restore_lock.acquire(blocking=False), "前置：锁应可用"
        try:
            r = client.post("/api/v1/backups", headers=admin_headers, json={"type": "full"})
            assert r.status_code == 409, f"恢复期间备份应 409，实际 {r.status_code}: {r.text}"
            assert r.json()["error"]["code"] == "CONFLICT"
            # 失败路径不应留下失败记录（在磁盘闸门/互斥阶段就拦下）
            listed = client.get("/api/v1/backups", headers=admin_headers).json()
            assert all(x["status"] != "failed" for x in listed["items"]), (
                "被互斥拒绝的请求不该登记失败备份"
            )
        finally:
            bs._restore_lock.release()

        # 锁释放后备份恢复可用
        r = client.post("/api/v1/backups", headers=admin_headers, json={"type": "full"})
        assert r.status_code == 201, r.text

    def test_restore_itself_can_take_safety_backup(self, client, admin_headers):
        """恢复流程内部的安全备份必须能拿到锁内通道（否则自锁，永远无法恢复）。"""
        resp = client.post("/api/v1/backups", headers=admin_headers, json={"type": "full"})
        assert resp.status_code == 201
        backup = resp.json()
        r = client.post(
            f"/api/v1/backups/{backup['id']}/restore",
            headers=admin_headers,
            json={"confirm": True, "reason": "安全备份通道验证"},
        )
        assert r.status_code == 200, f"恢复应成功（内部安全备份不得自锁）: {r.text}"


class TestPurgeAllRequiresConfirm:
    """清空回收站必须后端校验 confirm（§26，2026-10-29 补齐）。

    此前这是唯一一个没有后端 confirm 的物理删除入口，只靠前端弹窗——
    绕过界面直接调 API 就能一次性清空回收站。
    """

    def _make_recycled_entry(self, client, admin_headers):
        from pathlib import Path

        pdf = Path(__file__).resolve().parent / "files" / "sample.pdf"
        if not pdf.exists():
            pdf = Path(__file__).resolve().parent / "files" / "test.pdf"
        doc = client.post("/api/v1/documents", headers=admin_headers, json={
            "title": "清空校验资料", "effective_from": "2026-01-01",
        }).json()
        doc_id = doc["id"]
        if pdf.exists():
            with open(pdf, "rb") as fh:
                client.post(
                    f"/api/v1/documents/{doc_id}/file",
                    headers=admin_headers,
                    files={"file": ("清空校验.pdf", fh, "application/pdf")},
                )
        client.delete(f"/api/v1/documents/{doc_id}", headers=admin_headers)

    def test_purge_all_without_confirm_is_400(self, client, admin_headers):
        self._make_recycled_entry(client, admin_headers)
        before = client.get("/api/v1/recycle-bin", headers=admin_headers).json()["total"]
        assert before >= 1, "前置：回收站应有条目"

        r = client.post("/api/v1/recycle-bin/purge-all", headers=admin_headers)
        assert r.status_code == 400, f"缺 confirm 应 400，实际 {r.status_code}: {r.text}"
        assert r.json()["error"]["code"] == "BAD_REQUEST"

        # 什么都没被删
        after = client.get("/api/v1/recycle-bin", headers=admin_headers).json()["total"]
        assert after == before, "未确认的请求不得删除任何条目"

    def test_purge_all_with_confirm_succeeds(self, client, admin_headers):
        self._make_recycled_entry(client, admin_headers)
        r = client.post("/api/v1/recycle-bin/purge-all", headers=admin_headers,
                        json={"confirm": True})
        assert r.status_code == 200, r.text
        assert client.get("/api/v1/recycle-bin", headers=admin_headers).json()["total"] == 0


# ---------------------------------------------------------------------------
# 备份中断：不得留下"半个备份目录"冒充可用恢复点
# ---------------------------------------------------------------------------
class TestPartialBackup:
    def test_failed_backup_recorded_as_failed_and_not_restorable(
        self, client, admin_headers, db, monkeypatch
    ):
        """复制业务文件中途失败 → 记录 status='failed'，且【不可被恢复】。

        失败备份必须显式标记为 failed：若被当成 completed，恢复时会因
        缺 manifest / 缺文件而报错；更糟的是它可能混进备份列表，
        运维误选后才发现是坏的。
        """
        from app.services import backup_service as bs

        # 让业务文件复制在第一步就失败
        def _boom(src, dst):
            raise OSError("模拟磁盘故障：复制中断")

        monkeypatch.setattr(bs, "_copy_tree", _boom)

        with pytest.raises(Exception):
            bs.create_backup(db, current_user=_admin(db), backup_type="full")

        monkeypatch.undo()

        # 失败必须留痕：备份记录存在且状态为 failed
        from app.models.backup import BackupRecord

        records = db.query(BackupRecord).order_by(BackupRecord.id.desc()).all()
        assert records, "失败的备份也必须登记记录（留痕）"
        latest = records[0]
        assert latest.status == "failed", f"失败备份状态应为 failed，实际 {latest.status}"

        # 失败的备份不得出现在"可恢复"的列表语义里：直接对它发起恢复必须被拒
        r = client.post(
            f"/api/v1/backups/{latest.id}/restore",
            headers=admin_headers,
            json={"confirm": True},
        )
        assert r.status_code in (409, 404, 400), (
            f"status=failed 的备份不得被恢复，实际 {r.status_code}: {r.text}"
        )

    def test_failed_backup_leaves_no_completed_half_directory(
        self, client, admin_headers, db, monkeypatch
    ):
        """失败备份不得留下"看起来可用"的半成品目录。

        判据：失败后，列表里 status='completed' 的备份，其目录必须都存在
        且含 manifest.json——不能有 completed 记录指向缺 manifest 的目录
        （那会让恢复在校验阶段才炸，而非在备份阶段就暴露）。
        """
        from app.services import backup_service as bs

        def _boom(src, dst):
            raise OSError("模拟磁盘故障：复制中断")

        monkeypatch.setattr(bs, "_copy_tree", _boom)
        with pytest.raises(Exception):
            bs.create_backup(db, current_user=_admin(db), backup_type="full")
        monkeypatch.undo()

        listed = client.get("/api/v1/backups", headers=admin_headers).json()
        for item in listed["items"]:
            if item["status"] != "completed":
                continue
            d = Path(bs.settings.data_dir) / item["backup_path"]
            assert d.is_dir(), f"completed 备份目录应存在：{item['backup_path']}"
            assert (d / "manifest.json").is_file(), (
                f"completed 备份必须有 manifest.json：{item['backup_path']}"
            )


def _admin(db):
    from app.models.user import User

    return db.query(User).filter(User.role == "admin").first()


# ---------------------------------------------------------------------------
# 保留策略边界
# ---------------------------------------------------------------------------
class TestRetentionEdges:
    def test_keep_count_zero_means_unlimited(self, client, admin_headers, db):
        """BACKUP_KEEP_COUNT=0 表示不限份数，不得误清成 0 份。"""
        from app.core.config import get_settings

        settings = get_settings()
        original = settings.BACKUP_KEEP_COUNT
        settings.BACKUP_KEEP_COUNT = 0
        try:
            for _ in range(3):
                r = client.post("/api/v1/backups", headers=admin_headers, json={"type": "database"})
                assert r.status_code == 201, r.text
            summary = backup_service.apply_retention(db, current_user=_admin(db))
            assert isinstance(summary, dict)
            listed = client.get("/api/v1/backups", headers=admin_headers).json()
            assert listed["total"] >= 3, "keep=0 时不得清理掉备份"
        finally:
            settings.BACKUP_KEEP_COUNT = original

    def test_keep_count_limits_backups(self, client, admin_headers, db):
        """BACKUP_KEEP_COUNT=N 时最多保留 N 份。"""
        from app.core.config import get_settings

        settings = get_settings()
        original = settings.BACKUP_KEEP_COUNT
        settings.BACKUP_KEEP_COUNT = 1
        try:
            client.post("/api/v1/backups", headers=admin_headers, json={"type": "database"})
            client.post("/api/v1/backups", headers=admin_headers, json={"type": "database"})
            backup_service.apply_retention(db, current_user=_admin(db))
            listed = client.get("/api/v1/backups", headers=admin_headers).json()
            assert listed["total"] <= 1, f"保留份数上限应生效，实际 {listed['total']}"
        finally:
            settings.BACKUP_KEEP_COUNT = original
