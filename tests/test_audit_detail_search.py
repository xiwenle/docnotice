"""操作日志服务端操作人搜索与完整详情（2026-11-11）。"""

from __future__ import annotations

from app.services import audit_service


class TestAuditOperatorSearch:
    def test_username_search_matches_account_and_display_name(
        self, client, admin_headers, admin_user, normal_user, db
    ):
        audit_service.log(
            db,
            action=audit_service.ACTION_CREATE_TAG,
            user=admin_user,
            object_label="管理员动作",
            detail={"marker": "admin-only"},
            commit=True,
        )
        audit_service.log(
            db,
            action=audit_service.ACTION_CREATE_TAG,
            user=normal_user,
            object_label="普通用户动作",
            detail={"marker": "normal-only"},
            commit=True,
        )

        by_account = client.get("/api/v1/audit-logs?username=zhang", headers=admin_headers)
        assert by_account.status_code == 200
        body = by_account.json()
        assert body["total"] == 1
        assert body["items"][0]["username"] == "zhangsan"

        by_display_name = client.get("/api/v1/audit-logs?username=张三", headers=admin_headers)
        assert by_display_name.status_code == 200
        body = by_display_name.json()
        assert body["total"] == 1
        assert body["items"][0]["object_label"] == "普通用户动作"

    def test_single_detail_returns_full_detail_payload(self, client, admin_headers, admin_user, db):
        entry = audit_service.log(
            db,
            action=audit_service.ACTION_BACKUP_VERIFY,
            user=admin_user,
            object_type="backup",
            object_id=77,
            object_label="backup_20261111_120000",
            detail={
                "ok": False,
                "checks": {"manifest": {"ok": True}},
                "nested": {"message": "完整详情不得截断"},
            },
            ip="127.0.0.1",
            user_agent="pytest-agent",
            commit=True,
        )

        resp = client.get(f"/api/v1/audit-logs/{entry.id}", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"] == "backup_verify"
        assert body["object_id"] == 77
        assert body["ip"] == "127.0.0.1"
        assert body["user_agent"] == "pytest-agent"
        assert "完整详情不得截断" in body["detail"]
        assert "manifest" in body["detail"]
