"""操作日志测试（项目规范 §25、API.md §9、DESIGN §9）。

覆盖：关键操作产生审计（登录/登录失败/登出/改密/建分类/建标签/建用户/
建资料/上传替换/发布通知/阅读/确认/归档/恢复/备份/恢复/回收站恢复）、
查询过滤与分页、普通用户 403（权限文件另有全量矩阵）。
"""
from __future__ import annotations


from files.generators import make_pdf
from helpers import create_document, login_headers, upload_document_file


def _audit(client, headers, **query) -> dict:
    qs = "&".join(f"{k}={v}" for k, v in query.items())
    resp = client.get(f"/api/v1/audit-logs?{qs}" if qs else "/api/v1/audit-logs",
                      headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestAuthAudit:
    def test_login_and_failed_audited(self, client, admin_user, login):
        token = login(admin_user.username, "admin1234")
        headers = {"Authorization": f"Bearer {token}"}
        client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-1"})

        logs = _audit(client, headers, action="login")
        assert logs["total"] >= 1
        assert logs["items"][0]["username"] == "admin"
        assert logs["items"][0]["action"] == "login"

        failed = _audit(client, headers, action="login_failed")
        assert failed["total"] >= 1
        assert failed["items"][0]["username"] == "admin"

    def test_logout_audited(self, client, admin_user, login):
        token = login(admin_user.username, "admin1234")
        headers = {"Authorization": f"Bearer {token}"}
        client.post("/api/v1/auth/logout", headers=headers)
        # 2026-11-11 口径：登出会递增 token_version 使旧会话立即失效；
        # 查审计必须重新登录，而不是复用已注销的旧令牌。
        token = login(admin_user.username, "admin1234")
        headers = {"Authorization": f"Bearer {token}"}
        logs = _audit(client, headers, action="logout")
        assert logs["total"] >= 1

    def test_change_password_audited(self, client, admin_user, login):
        token = login(admin_user.username, "admin1234")
        headers = {"Authorization": f"Bearer {token}"}
        client.post("/api/v1/auth/change-password", headers=headers, json={
            "old_password": "admin1234", "new_password": "newpass-777",
        })
        # 单点登录（2026-09-03）：改密自增 token_version，改密前令牌全部作废，
        # 用新密码重新登录换取有效令牌（当前设备的续命 Cookie 仅浏览器场景）
        token = login(admin_user.username, "newpass-777")
        headers = {"Authorization": f"Bearer {token}"}
        logs = _audit(client, headers, action="change_password")
        assert logs["total"] >= 1


class TestManageAudit:
    def test_category_tag_audited(self, client, admin_headers):
        client.post("/api/v1/categories", headers=admin_headers, json={"name": "审计分类"})
        client.post("/api/v1/tags", headers=admin_headers, json={"name": "审计标签"})
        assert _audit(client, admin_headers, action="create_category")["total"] == 1
        assert _audit(client, admin_headers, action="create_tag")["total"] == 1

    def test_user_management_audited(self, client, admin_headers):
        created = client.post("/api/v1/users", headers=admin_headers, json={
            "username": "审计用户", "password": "password123", "display_name": "审计用户",
        }).json()
        client.put(f"/api/v1/users/{created['id']}", headers=admin_headers,
                   json={"display_name": "改名"})
        client.post(f"/api/v1/users/{created['id']}/reset-password", headers=admin_headers,
                    json={"new_password": "newpass-888"})
        client.delete(f"/api/v1/users/{created['id']}", headers=admin_headers)
        logs = _audit(client, admin_headers, action="manage_user")
        assert logs["total"] == 4  # 创建/修改/重置密码/停用


class TestDocumentAudit:
    def test_document_upload_archive_restore_audited(self, client, admin_headers, tmp_path):
        doc_id = create_document(client, admin_headers, title="审计资料",
                                 effective_from="2026-01-01")["id"]
        pdf = make_pdf(tmp_path / "审计.pdf")
        upload_document_file(client, admin_headers, doc_id, filename="审计.pdf",
                             content=pdf.read_bytes())
        client.request("DELETE", f"/api/v1/documents/{doc_id}", headers=admin_headers,
                       json={"reason": "审计测试"})
        client.post(f"/api/v1/documents/{doc_id}/restore", headers=admin_headers)

        assert _audit(client, admin_headers, action="create_document")["total"] == 1
        logs = _audit(client, admin_headers, action="replace_file")["items"]
        assert len(logs) == 1
        assert logs[0]["object_id"] == doc_id
        assert _audit(client, admin_headers, action="archive_document")["total"] == 1
        assert _audit(client, admin_headers, action="restore_document")["total"] == 1


class TestNoticeAudit:
    def test_notice_lifecycle_audited(self, client, admin_headers, user_factory):
        user_factory("审计读者", role="user", password="user1234")
        notice = client.post("/api/v1/notices", headers=admin_headers, json={
            "title": "审计通知", "content": "x", "notice_type": "发布",
        }).json()
        notice_id = notice["id"]
        reader = login_headers(client, "审计读者", "user1234")
        client.post(f"/api/v1/notices/{notice_id}/read", headers=reader)
        client.post(f"/api/v1/notices/{notice_id}/cancel", headers=admin_headers,
                    json={"reason": "审计取消"})

        assert _audit(client, admin_headers, action="create_notice")["total"] == 1
        assert _audit(client, admin_headers, action="publish_notice")["total"] == 1
        read_logs = _audit(client, admin_headers, action="read_notice")["items"]
        assert read_logs[0]["username"] == "审计读者"
        assert _audit(client, admin_headers, action="cancel_notice")["total"] == 1

    def test_recycle_restore_audited(self, client, admin_headers, tmp_path):
        doc_id = create_document(client, admin_headers, title="回收审计",
                                 effective_from="2026-01-01")["id"]
        pdf = make_pdf(tmp_path / "a.pdf")
        upload_document_file(client, admin_headers, doc_id, filename="a.pdf", content=pdf.read_bytes())
        upload_document_file(client, admin_headers, doc_id, filename="b.pdf", content=pdf.read_bytes())
        entry_id = client.get("/api/v1/recycle-bin", headers=admin_headers).json()["items"][0]["id"]
        client.post(f"/api/v1/recycle-bin/{entry_id}/restore", headers=admin_headers)
        assert _audit(client, admin_headers, action="recycle_restore")["total"] == 1

    def test_backup_audited(self, client, admin_headers):
        backup = client.post("/api/v1/backups", headers=admin_headers, json={"type": "full"})
        assert backup.status_code == 201
        backup_id = backup.json()["id"]
        logs = _audit(client, admin_headers, action="backup_create")
        assert logs["total"] == 1
        assert logs["items"][0]["object_id"] == backup_id


class TestAuditQuery:
    def test_query_filters_and_pagination(self, client, admin_headers):
        client.post("/api/v1/tags", headers=admin_headers, json={"name": "过滤标签1"})
        client.post("/api/v1/tags", headers=admin_headers, json={"name": "过滤标签2"})
        client.post("/api/v1/categories", headers=admin_headers, json={"name": "过滤分类"})

        by_action = _audit(client, admin_headers, action="create_tag")
        assert by_action["total"] == 2 and by_action["pages"] == 1
        by_object = _audit(client, admin_headers, object_type="category")
        assert by_object["total"] >= 1
        page = _audit(client, admin_headers, page=1, page_size=1)
        assert len(page["items"]) == 1
        assert page["total"] >= 3
        assert page["page"] == 1 and page["page_size"] == 1

    def test_single_log_detail(self, client, admin_headers):
        client.post("/api/v1/tags", headers=admin_headers, json={"name": "单条日志"})
        log = _audit(client, admin_headers, action="create_tag")["items"][0]
        detail = client.get(f"/api/v1/audit-logs/{log['id']}", headers=admin_headers)
        assert detail.status_code == 200
        body = detail.json()
        assert body["action"] == "create_tag"
        assert body["object_label"] == "单条日志"

    def test_invalid_log_404(self, client, admin_headers):
        assert client.get("/api/v1/audit-logs/99999", headers=admin_headers).status_code == 404

    def test_user_cannot_query(self, client, user_headers):
        assert client.get("/api/v1/audit-logs", headers=user_headers).status_code == 403

# ---------------------------------------------------------------------------
# 操作日志删除（2026-08-30 拍板：日志可删；删除动作本身写汇总审计）
# ---------------------------------------------------------------------------
class TestAuditLogDelete:
    def test_batch_delete_and_summary_audit(self, client, admin_user, login):
        # 两次登录产生 login 审计；单点登录下只有最后一次的令牌有效
        login(admin_user.username, "admin1234")
        token = login(admin_user.username, "admin1234")
        headers = {"Authorization": f"Bearer {token}"}
        items = _audit(client, headers, action="login")["items"]
        ids = [i["id"] for i in items[:2]]

        r = client.post("/api/v1/audit-logs/batch-delete", headers=headers, json={"ids": ids})
        assert r.status_code == 200, r.text
        assert sorted(r.json()["deleted"]) == sorted(ids)

        # 删除动作本身可追溯（audit_delete 汇总）
        summary = _audit(client, headers, action="audit_delete")
        assert summary["total"] == 1
        detail = summary["items"][0].get("detail") or ""
        assert all(str(i) in detail for i in ids)

        # 被删行确实消失
        rest_ids = {i["id"] for i in _audit(client, headers)["items"]}
        assert not (set(ids) & rest_ids)

    def test_delete_single(self, client, admin_user, login):
        token = login(admin_user.username, "admin1234")  # 产生登录审计并取得有效令牌
        headers = {"Authorization": f"Bearer {token}"}
        target = _audit(client, headers, action="login")["items"][0]["id"]
        r = client.request("DELETE", f"/api/v1/audit-logs/{target}", headers=headers)
        assert r.status_code == 200 and target in r.json()["deleted"]
        assert _audit(client, headers, action="login")["total"] == 0

    def test_empty_ids_rejected(self, client, admin_headers):
        r = client.post("/api/v1/audit-logs/batch-delete", headers=admin_headers, json={"ids": []})
        assert r.status_code == 400

    def test_normal_user_forbidden(self, client, user_headers):
        r = client.post("/api/v1/audit-logs/batch-delete", headers=user_headers, json={"ids": [1]})
        assert r.status_code == 403
        r = client.request("DELETE", "/api/v1/audit-logs/1", headers=user_headers)
        assert r.status_code == 403
