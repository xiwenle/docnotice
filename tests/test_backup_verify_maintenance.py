"""备份校验与恢复维护闸门（2026-11-11）。"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.core import maintenance
from app.services import backup_service


def _create_full_backup(client, headers) -> dict:
    resp = client.post("/api/v1/backups", headers=headers, json={"type": "full"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _backup_dir(data_dir, backup: dict):
    return data_dir / backup["backup_path"]


def _rel_file(backup_dir):
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    return next(iter(manifest["files"]))


class TestBackupVerify:
    def test_verify_all_checks_without_restoring(self, client, admin_headers, data_dir):
        source = data_dir / "documents" / "verify-source.bin"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"verify-me")
        backup = _create_full_backup(client, admin_headers)
        bdir = _backup_dir(data_dir, backup)

        resp = client.post(f"/api/v1/backups/{backup['id']}/verify", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["checked_at"]
        assert body["checks"]["manifest"]["ok"] is True
        assert body["checks"]["db_sha256"]["ok"] is True
        assert body["checks"]["file_sha256"]["ok"] is True
        assert body["checks"]["file_sha256"]["checked"] >= 1
        assert body["checks"]["sqlite_integrity_check"]["ok"] is True
        assert body["checks"]["sqlite_integrity_check"]["result"] == "ok"

        # 校验不是恢复：备份目录、manifest 与记录状态保持不变。
        assert bdir.is_dir() and (bdir / "manifest.json").is_file()
        detail = client.get(f"/api/v1/backups/{backup['id']}", headers=admin_headers)
        assert detail.status_code == 200
        assert detail.json()["status"] == "completed"

        logs = client.get("/api/v1/audit-logs?action=backup_verify", headers=admin_headers).json()
        assert logs["total"] == 1
        assert logs["items"][0]["object_id"] == backup["id"]

    def test_verify_admin_only(self, client, admin_headers, user_headers):
        backup = _create_full_backup(client, admin_headers)
        resp = client.post(f"/api/v1/backups/{backup['id']}/verify", headers=user_headers)
        assert resp.status_code == 403

    def test_manifest_tamper_detected(self, client, admin_headers, data_dir):
        backup = _create_full_backup(client, admin_headers)
        bdir = _backup_dir(data_dir, backup)
        (bdir / "manifest.json").write_text("{broken", encoding="utf-8")

        body = client.post(f"/api/v1/backups/{backup['id']}/verify", headers=admin_headers).json()
        assert body["ok"] is False
        assert body["checks"]["manifest"]["ok"] is False

    def test_file_sha_tamper_detected(self, client, admin_headers, data_dir):
        source = data_dir / "documents" / "tamper-source.bin"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"original")
        backup = _create_full_backup(client, admin_headers)
        bdir = _backup_dir(data_dir, backup)
        rel = _rel_file(bdir)
        with (bdir / "files" / rel).open("ab") as fh:
            fh.write(b"tampered")

        body = client.post(f"/api/v1/backups/{backup['id']}/verify", headers=admin_headers).json()
        assert body["ok"] is False
        assert body["checks"]["file_sha256"]["ok"] is False
        assert any(rel in item for item in body["checks"]["file_sha256"]["failed"])

    def test_db_sha_tamper_detected(self, client, admin_headers, data_dir):
        backup = _create_full_backup(client, admin_headers)
        bdir = _backup_dir(data_dir, backup)
        with (bdir / "app.db").open("ab") as fh:
            fh.write(b"tampered-db")

        body = client.post(f"/api/v1/backups/{backup['id']}/verify", headers=admin_headers).json()
        assert body["ok"] is False
        assert body["checks"]["db_sha256"]["ok"] is False

    def test_sqlite_integrity_check_detects_corruption(self, client, admin_headers, data_dir, db):
        backup = _create_full_backup(client, admin_headers)
        bdir = _backup_dir(data_dir, backup)
        corrupt = b"not-a-sqlite-database"
        (bdir / "app.db").write_bytes(corrupt)
        digest = hashlib.sha256(corrupt).hexdigest()

        manifest_file = bdir / "manifest.json"
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        manifest["db_sha256"] = digest
        manifest_file.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        from app.models.backup import BackupRecord

        record = db.get(BackupRecord, backup["id"])
        assert record is not None
        record.db_sha256 = digest
        db.commit()

        body = client.post(f"/api/v1/backups/{backup['id']}/verify", headers=admin_headers).json()
        assert body["ok"] is False
        assert body["checks"]["db_sha256"]["ok"] is True
        assert body["checks"]["sqlite_integrity_check"]["ok"] is False


class TestMaintenanceGate:
    def test_business_api_503_but_health_allowed(self, client, admin_headers):
        maintenance.enter_maintenance()
        try:
            health = client.get("/health")
            assert health.status_code == 200

            blocked = client.get("/api/v1/auth/me", headers=admin_headers)
            assert blocked.status_code == 503
            assert blocked.headers["retry-after"] == "5"
            assert blocked.json()["error"]["code"] == "MAINTENANCE_MODE"
        finally:
            maintenance.leave_maintenance()

        assert client.get("/api/v1/auth/me", headers=admin_headers).status_code == 200

    def test_restore_releases_gate_on_success(self, db, admin_user, monkeypatch):
        def fake_impl(*args, **kwargs):
            assert maintenance.is_maintenance() is True
            return {"ok": True}

        monkeypatch.setattr(backup_service, "_restore_backup_impl", fake_impl)
        result = backup_service.restore_backup(db, current_user=admin_user, backup_id=1, confirm=True)
        assert result == {"ok": True}
        assert maintenance.is_maintenance() is False
        assert backup_service._restore_lock.locked() is False

    def test_restore_releases_gate_on_failure(self, db, admin_user, monkeypatch):
        def fake_impl(*args, **kwargs):
            assert maintenance.is_maintenance() is True
            raise RuntimeError("restore failed")

        monkeypatch.setattr(backup_service, "_restore_backup_impl", fake_impl)
        with pytest.raises(RuntimeError, match="restore failed"):
            backup_service.restore_backup(db, current_user=admin_user, backup_id=1, confirm=True)
        assert maintenance.is_maintenance() is False
        assert backup_service._restore_lock.locked() is False
