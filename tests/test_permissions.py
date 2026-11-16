"""权限测试（项目规范 §23/§33、API.md §0.1/§3、DESIGN §4.2）。

原则：权限由后端强制校验，不能仅靠前端隐藏（§33）。
覆盖：
- 未登录访问任意端点 → 401 UNAUTHORIZED
- 普通用户访问全部管理端点 → 403 FORBIDDEN
- 停用用户：登录 403、已签发令牌请求 403
- 权限模型 v3：任意 admin 具完整管理权；普通用户访问管理端点一律 403
- 普通用户不可读归档资料/未发布通知/非本人通知
- 普通用户不能修改资料/通知/时效/看日志
"""
from __future__ import annotations

import pytest

from files.generators import make_pdf
from helpers import create_document, login_headers, upload_document_file

ADMIN_MANAGE_ENDPOINTS = [
    ("POST", "/api/v1/documents", {"title": "x", "effective_from": "2026-01-01"}),
    ("PUT", "/api/v1/documents/1", {"title": "x"}),
    ("POST", "/api/v1/documents/1/file", "FILE"),
    ("DELETE", "/api/v1/documents/1", None),
    ("POST", "/api/v1/documents/1/restore", None),
    ("POST", "/api/v1/notices", {"title": "x", "content": "y", "notice_type": "发布"}),
    ("PUT", "/api/v1/notices/1", {"title": "x"}),
    ("POST", "/api/v1/notices/1/cancel", {"reason": "r"}),
    ("POST", "/api/v1/users", {"username": "u", "password": "password123", "display_name": "U"}),
    ("PUT", "/api/v1/users/1", {"display_name": "U"}),
    ("DELETE", "/api/v1/users/1", None),
    ("POST", "/api/v1/departments", {"name": "部门"}),
    ("PUT", "/api/v1/departments/1", {"name": "部门"}),
    ("DELETE", "/api/v1/departments/1", None),
    ("POST", "/api/v1/categories", {"name": "分类"}),
    ("PUT", "/api/v1/categories/1", {"name": "分类"}),
    ("DELETE", "/api/v1/categories/1", None),
    ("POST", "/api/v1/tags", {"name": "标签"}),
    ("PUT", "/api/v1/tags/1", {"name": "标签"}),
    ("DELETE", "/api/v1/tags/1", None),
    ("POST", "/api/v1/backups", {"type": "full"}),
]


class TestUnauthenticated:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/v1/documents"),
            ("GET", "/api/v1/documents/1"),
            ("GET", "/api/v1/documents/1/status"),
            ("GET", "/api/v1/documents/1/file"),
            ("GET", "/api/v1/documents/1/preview/pdf"),
            ("GET", "/api/v1/notices"),
            ("GET", "/api/v1/notices/1"),
            ("GET", "/api/v1/users"),
            ("GET", "/api/v1/audit-logs"),
            ("GET", "/api/v1/backups"),
            ("GET", "/api/v1/recycle-bin"),
            ("GET", "/api/v1/departments"),
            ("GET", "/api/v1/categories"),
            ("GET", "/api/v1/tags"),
            ("GET", "/api/v1/auth/me"),
        ],
    )
    def test_unauthenticated_401(self, client, method, path):
        resp = client.request(method, path)
        assert resp.status_code == 401, f"{method} {path}: {resp.status_code}"
        assert resp.json()["error"]["code"] == "UNAUTHORIZED"

    def test_bad_token_401(self, client):
        resp = client.get("/api/v1/documents", headers={"Authorization": "Bearer invalid.token.xxx"})
        assert resp.status_code == 401


class TestNormalUserForbidden:
    @pytest.mark.parametrize("method,path,body", ADMIN_MANAGE_ENDPOINTS)
    def test_all_admin_endpoints_403(self, client, user_headers, method, path, body):
        if body == "FILE":
            resp = client.post(path, files={"file": ("x.pdf", b"%PDF-1.4 x", "application/pdf")},
                               headers=user_headers)
        else:
            resp = client.request(method, path, json=body, headers=user_headers)
        assert resp.status_code == 403, f"{method} {path}: {resp.status_code} {resp.text}"
        assert resp.json()["error"]["code"] == "FORBIDDEN"

    def test_list_users_403(self, client, user_headers):
        resp = client.get("/api/v1/users", headers=user_headers)
        assert resp.status_code == 403

    def test_get_audit_logs_403(self, client, user_headers):
        assert client.get("/api/v1/audit-logs", headers=user_headers).status_code == 403
        assert client.get("/api/v1/audit-logs/1", headers=user_headers).status_code == 403

    def test_backups_and_recycle_403(self, client, user_headers):
        assert client.get("/api/v1/backups", headers=user_headers).status_code == 403
        assert client.post("/api/v1/backups", json={"type": "full"}, headers=user_headers).status_code == 403
        assert client.get("/api/v1/recycle-bin", headers=user_headers).status_code == 403
        assert client.post("/api/v1/recycle-bin/1/restore", headers=user_headers).status_code == 403
        assert client.request("DELETE", "/api/v1/recycle-bin/1", json={"confirm": True},
                              headers=user_headers).status_code == 403

    def test_cannot_modify_document(self, client, user_headers, admin_headers):
        """普通用户不能改资料/时效（§33）——后端校验。"""
        doc_id = create_document(client, admin_headers, title="受保护", effective_from="2026-01-01")["id"]
        resp = client.put(f"/api/v1/documents/{doc_id}", headers=user_headers,
                          json={"title": "篡改", "effective_to": "2099-01-01"})
        assert resp.status_code == 403


class TestDisabledUser:
    def test_disabled_user_login_403(self, client, admin_headers, user_factory):
        u = user_factory("被停用", role="user", password="user1234")
        resp = client.put(f"/api/v1/users/{u.id}", headers=admin_headers,
                          json={"is_active": False})
        assert resp.status_code == 200
        # 登录被拒（FORBIDDEN 而非 401）
        resp = client.post("/api/v1/auth/login",
                           json={"username": "被停用", "password": "user1234"})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN"

    def test_disabled_user_token_rejected(self, client, user_factory, admin_headers):
        """停用后旧令牌失效：get_current_user 复核激活状态（DESIGN §4.2）。"""
        from app.core.security import create_access_token

        u = user_factory("待停用令牌", role="user")
        token = create_access_token(user_id=u.id, username=u.username, role=u.role)
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/v1/documents", headers=headers).status_code == 200
        client.put(f"/api/v1/users/{u.id}", headers=admin_headers, json={"is_active": False})
        resp = client.get("/api/v1/documents", headers=headers)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN"


class TestAdminFullPowers:
    """权限模型 v3：两角色——任意 admin 均具完整管理权（v2 的 admin/superadmin 分层已合并）。"""

    def _admin_only_headers(self, client, user_factory):
        user_factory("经理", role="admin", password="manager1234")
        return login_headers(client, "经理", "manager1234")

    def test_admin_can_grant_admin_role(self, client, user_factory):
        mgr_headers = self._admin_only_headers(client, user_factory)
        resp = client.post("/api/v1/users", headers=mgr_headers, json={
            "username": "新管理员", "password": "password123",
            "display_name": "新管理员", "role": "admin",
        })
        assert resp.status_code == 201, resp.text

    def test_admin_can_create_user(self, client, user_factory):
        """v3：admin 可创建普通用户（v2 中用户管理整体仅 superadmin，已合并）。"""
        mgr_headers = self._admin_only_headers(client, user_factory)
        resp = client.post("/api/v1/users", headers=mgr_headers, json={
            "username": "普通新员工", "password": "password123",
            "display_name": "普通新员工", "role": "user",
        })
        assert resp.status_code == 201, resp.text

    def test_admin_backup_restore_gated_by_confirm_not_role(
        self, client, user_factory, admin_headers
    ):
        """v3：备份恢复/删除对任意 admin 开放；无 confirm → 400（权限通过、卡在二次确认）。"""
        mgr_headers = self._admin_only_headers(client, user_factory)
        backup = client.post("/api/v1/backups", headers=admin_headers, json={"type": "full"})
        assert backup.status_code == 201
        backup_id = backup.json()["id"]
        # 无 confirm → 400 而非 403：证明 admin 权限层已通过
        resp = client.post(f"/api/v1/backups/{backup_id}/restore", headers=mgr_headers)
        assert resp.status_code == 400, resp.text
        # 删除备份：admin + confirm → 成功（不真恢复，避免换库）
        resp = client.request("DELETE", f"/api/v1/backups/{backup_id}",
                              headers=mgr_headers, json={"confirm": True})
        assert resp.status_code in (200, 204), resp.text

    def test_admin_recycle_purge_permitted_with_confirm(
        self, client, user_factory, admin_headers, tmp_path
    ):
        """v3：回收站 purge 对 admin 开放；无 confirm → 400，带 confirm → 物理删除。"""
        mgr_headers = self._admin_only_headers(client, user_factory)
        doc_id = create_document(client, admin_headers, title="待清除",
                                 effective_from="2026-01-01")["id"]
        pdf = make_pdf(tmp_path / "x.pdf")
        upload_document_file(client, admin_headers, doc_id, filename="x.pdf", content=pdf.read_bytes())
        client.request("DELETE", f"/api/v1/documents/{doc_id}", headers=admin_headers)
        entry_id = client.get("/api/v1/recycle-bin", headers=admin_headers).json()["items"][0]["id"]
        resp = client.request("DELETE", f"/api/v1/recycle-bin/{entry_id}", headers=mgr_headers)
        assert resp.status_code == 400, f"无 confirm 应 400（非 403）: {resp.text}"
        resp = client.request("DELETE", f"/api/v1/recycle-bin/{entry_id}",
                              headers=mgr_headers, json={"confirm": True})
        assert resp.status_code in (200, 204), resp.text


class TestContentVisibility:
    def test_user_cannot_read_archived_document_file(self, client, admin_headers, user_headers, tmp_path):
        pdf = make_pdf(tmp_path / "源.pdf")
        doc_id = create_document(client, admin_headers, title="归档保密",
                                 effective_from="2026-01-01")["id"]
        upload_document_file(client, admin_headers, doc_id, filename="源.pdf", content=pdf.read_bytes())
        client.request("DELETE", f"/api/v1/documents/{doc_id}", headers=admin_headers)
        assert client.get(f"/api/v1/documents/{doc_id}", headers=user_headers).status_code == 403
        assert client.get(f"/api/v1/documents/{doc_id}/file", headers=user_headers).status_code == 403
        assert client.get(f"/api/v1/documents/{doc_id}/preview/pdf", headers=user_headers).status_code == 403
        assert client.get(f"/api/v1/documents/{doc_id}/status", headers=user_headers).status_code == 403

    def test_user_cannot_see_not_yet_effective_notice(
        self, client, admin_headers, user_headers, normal_user
    ):
        """未到生效时间的通知对普通用户不可见、不可读。

        2026-11-04：草稿下线，改用「已发布但未生效」这一等效边界。
        """
        resp = client.post("/api/v1/notices", headers=admin_headers, json={
            "title": "未来生效通知", "content": "内容", "notice_type": "发布",
            "effective_at": "2099-01-01T00:00:00",
        })
        assert resp.status_code == 201, resp.text
        notice_id = resp.json()["id"]
        assert client.get(f"/api/v1/notices/{notice_id}",
                          headers=user_headers).status_code in (403, 404)
        listed = client.get("/api/v1/notices", headers=user_headers).json()
        assert listed["total"] == 0

    def test_user_cannot_read_notice_not_targeting_them(
        self, client, admin_headers, user_headers, user_factory
    ):
        """target=user 模式：非目标用户 403（§21/§33）。"""
        other = user_factory("目标用户", role="user", password="user1234")
        resp = client.post("/api/v1/notices", headers=admin_headers, json={
            "title": "指定阅读", "content": "仅目标用户可见", "notice_type": "发布",
            "target": {"scope": "user", "user_ids": [other.id]},
        })
        assert resp.status_code == 201
        notice_id = resp.json()["id"]
        # 普通用户 zhangsan 不在 target → 403（详情与阅读）
        assert client.get(f"/api/v1/notices/{notice_id}", headers=user_headers).status_code == 403
        assert client.post(f"/api/v1/notices/{notice_id}/read", headers=user_headers).status_code == 403
        # 目标用户可以看
        target_headers = login_headers(client, "目标用户", "user1234")
        assert client.get(f"/api/v1/notices/{notice_id}", headers=target_headers).status_code == 200

    def test_download_follows_view_permission(
        self, client, admin_headers, user_headers, user_factory, tmp_path
    ):
        """下载权限 = 查看权限（2026-11-04：allow_download 开关已移除）。

        - 能看到的资料（audience 命中）→ 可预览、可下载
        - 看不到的资料（不在阅读对象内）→ 预览与下载都 404/403
        """
        pdf = make_pdf(tmp_path / "物.pdf")
        doc_id = create_document(client, admin_headers, title="下载分级",
                                 effective_from="2026-01-01")["id"]
        upload_document_file(client, admin_headers, doc_id,
                             filename="物.pdf", content=pdf.read_bytes())

        # 默认 audience=all：普通用户既可预览也可下载
        assert client.get(f"/api/v1/documents/{doc_id}/preview/pdf",
                          headers=user_headers).status_code == 200
        assert client.get(f"/api/v1/documents/{doc_id}/file",
                          headers=user_headers).status_code == 200
        assert client.get(f"/api/v1/documents/{doc_id}/file",
                          headers=admin_headers).status_code == 200

        # 改成「指定人员」，但名单里只有别人 → 预览与下载都应被拒
        other = user_factory("别人", password="user1234", role="user")
        resp = client.put(f"/api/v1/documents/{doc_id}", headers=admin_headers,
                          json={"audience_scope": "user",
                                "audience_user_ids": [other.id]})
        assert resp.status_code == 200
        assert client.get(f"/api/v1/documents/{doc_id}/file",
                          headers=user_headers).status_code in (403, 404)
        assert client.get(f"/api/v1/documents/{doc_id}/preview/pdf",
                          headers=user_headers).status_code in (403, 404)