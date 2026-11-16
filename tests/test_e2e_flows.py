"""端到端集成测试：模拟完整用户流程（多步 API 调用链路）。

覆盖 项目规范 §38 最终验证要求：
- 登录 → 工作台 → 资料 CRUD → 预览 → 下载
- 通知创建 → 发布 → 阅读 → 确认 → 统计
- 管理员 → 普通用户角色切换

文件上传流程（项目规范 §8）：
1. POST /api/v1/documents  → 创建资料元数据（无文件）
2. POST /api/v1/documents/{doc_id}/file  → 上传/替换文件
"""
from __future__ import annotations

import io
import tempfile
from pathlib import Path


from files.generators import make_pdf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _login(client, username, password):
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"登录失败 {resp.status_code}: {resp.text}"
    return resp.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _create_doc(client, headers, title="测试资料", **extra):
    data = {"title": title, "effective_from": "2026-01-01", **extra}
    resp = client.post("/api/v1/documents", json=data, headers=headers)
    assert resp.status_code in (200, 201), f"创建资料失败: {resp.status_code} {resp.text}"
    return resp.json()


# 真实可解析 PDF 字节（上传有 pypdf 深度校验，假字节会被 415 拒绝，§32）
_PDF_MAGIC = make_pdf(
    Path(tempfile.mkdtemp(prefix="e2e-pdf-")) / "real.pdf", pages=1
).read_bytes()


def _upload_to_doc(client, headers, doc_id, content=None, name="doc.pdf"):
    resp = client.post(
        f"/api/v1/documents/{doc_id}/file",
        files={"file": (name, io.BytesIO(content or _PDF_MAGIC), "application/pdf")},
        headers=headers,
    )
    assert resp.status_code == 200, f"上传失败: {resp.status_code} {resp.text}"
    return resp.json()


def _ensure_notice_type(db):
    from app.models.notice import NoticeType
    nt = db.query(NoticeType).first()
    return nt.name if nt else "发布"


# ===================================================================
# 场景 1：登录 → 工作台
# ===================================================================
class TestE2E_LoginDashboard:
    def test_login_and_dashboard(self, client, db, admin_user):
        body = _login(client, "admin", "admin1234")
        token = body["access_token"]
        assert token
        resp = client.get("/", headers=_auth(token))
        assert resp.status_code == 200
        assert "工作台" in resp.text

    def test_login_wrong_password(self, client, admin_user):
        resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client, admin_user):
        resp = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "123"})
        assert resp.status_code == 401


# ===================================================================
# 场景 2：资料 CRUD + 上传 + 搜索 + 筛选
# ===================================================================
class TestE2E_DocumentCRUD:
    def test_create_upload_list_detail(self, client, db, admin_user, category, tag):
        body = _login(client, "admin", "admin1234")
        h = _auth(body["access_token"])

        doc = _create_doc(client, h, title="E2E测试资料",
                          summary="端到端测试", category_id=category.id,
                          tag_ids=[tag.id], effective_from="2026-01-01",
                          effective_to="2026-12-31")
        doc_id = doc["id"]
        _upload_to_doc(client, h, doc_id)

        resp = client.get("/api/v1/documents", headers=h)
        assert resp.status_code == 200
        assert "E2E测试资料" in [d["title"] for d in resp.json()["items"]]

        resp = client.get(f"/api/v1/documents/{doc_id}", headers=h)
        d = resp.json()
        assert d["title"] == "E2E测试资料"
        assert d["has_file"] is True
        assert d["category"]["name"] == "规章制度"

    def test_update_metadata(self, client, db, admin_user):
        body = _login(client, "admin", "admin1234")
        h = _auth(body["access_token"])
        doc = _create_doc(client, h, title="原始标题", effective_from="2026-01-01")

        resp = client.put(f"/api/v1/documents/{doc['id']}",
                          json={"title": "修改后"}, headers=h)
        assert resp.status_code == 200
        assert client.get(f"/api/v1/documents/{doc['id']}", headers=h).json()["title"] == "修改后"

    def test_archive_hides_from_user(self, client, db, admin_user, normal_user):
        body = _login(client, "admin", "admin1234")
        h = _auth(body["access_token"])
        doc = _create_doc(client, h, title="归档测试", effective_from="2026-01-01")

        resp = client.delete(f"/api/v1/documents/{doc['id']}", headers=h)
        assert resp.status_code == 200

        vh = _auth(_login(client, "zhangsan", "user1234")["access_token"])
        titles = [d["title"] for d in client.get("/api/v1/documents", headers=vh).json()["items"]]
        assert "归档测试" not in titles

    def test_keyword_search(self, client, db, admin_user):
        body = _login(client, "admin", "admin1234")
        h = _auth(body["access_token"])
        _create_doc(client, h, title="考勤管理规定", effective_from="2026-01-01")
        _create_doc(client, h, title="财务报销流程", effective_from="2026-01-01")

        items = client.get("/api/v1/documents?keyword=考勤", headers=h).json()["items"]
        assert len(items) >= 1
        assert any("考勤" in d["title"] for d in items)

    def test_file_replace(self, client, db, admin_user):
        body = _login(client, "admin", "admin1234")
        h = _auth(body["access_token"])
        doc = _create_doc(client, h, title="替换测试", effective_from="2026-01-01")
        _upload_to_doc(client, h, doc["id"])

        resp = client.post(
            f"/api/v1/documents/{doc['id']}/file",
            files={"file": ("new.pdf", io.BytesIO(_PDF_MAGIC), "application/pdf")},
            headers=h,
        )
        assert resp.status_code == 200


# ===================================================================
# 场景 3：通知全流程
# ===================================================================
class TestE2E_NoticeFlow:
    def test_full_lifecycle(self, client, db, admin_user, normal_user):
        type_name = _ensure_notice_type(db)
        body = _login(client, "admin", "admin1234")
        h = _auth(body["access_token"])

        resp = client.post("/api/v1/notices", json={
            "title": "E2E通知", "content": "测试正文",
            "notice_type": type_name, "effective_at": "2026-01-01T00:00:00",
            "target": {"scope": "user", "user_ids": [normal_user.id]},
        }, headers=h)
        assert resp.status_code in (200, 201)
        body = resp.json()
        nid = body["id"]
        # 2026-11-04：创建即发布，不再有单独的 /publish 步骤
        assert body["status"] == "published"
        assert body["target_user_count"] >= 1

        # 普通用户操作
        vh = _auth(_login(client, "zhangsan", "user1234")["access_token"])
        assert client.get(f"/api/v1/notices/{nid}", headers=vh).status_code == 200
        assert client.post(f"/api/v1/notices/{nid}/read", headers=vh).status_code == 200

        # 管理员统计（确认功能已下线，只统计查看）
        stats = client.get(f"/api/v1/notices/{nid}/statistics", headers=h).json()
        assert stats["target_user_count"] >= 1
        assert stats["viewed_count"] >= 1

    def test_deadline_does_not_block_read(self, client, db, admin_user, normal_user):
        """截止时间只影响"是否计入待读提醒"，不阻止打开查看。

        确认功能下线后（2026-11-03），原「截止后不可确认」的约束没有对应
        接口；此处验证截止通知仍可正常查看，语义未被误伤。
        """
        from datetime import date, timedelta
        type_name = _ensure_notice_type(db)
        body = _login(client, "admin", "admin1234")
        h = _auth(body["access_token"])
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        resp = client.post("/api/v1/notices", json={
            "title": "已截止通知", "content": "内容",
            "notice_type": type_name, "effective_at": "2026-01-01T00:00:00",
            "deadline_at": yesterday,
            "target": {"scope": "user", "user_ids": [normal_user.id]},
        }, headers=h)
        nid = resp.json()["id"]

        vh = _auth(_login(client, "zhangsan", "user1234")["access_token"])
        assert client.get(f"/api/v1/notices/{nid}", headers=vh).status_code == 200
        assert client.post(f"/api/v1/notices/{nid}/read", headers=vh).status_code == 200

    def test_keyword_filter(self, client, db, admin_user, normal_user):
        type_name = _ensure_notice_type(db)
        body = _login(client, "admin", "admin1234")
        h = _auth(body["access_token"])

        client.post("/api/v1/notices", json={
            "title": "人事通知A", "content": "c", "notice_type": type_name,
            "effective_at": "2026-01-01T00:00:00",
        }, headers=h)
        client.post("/api/v1/notices", json={
            "title": "财务通知B", "content": "c", "notice_type": type_name,
            "effective_at": "2026-01-01T00:00:00",
        }, headers=h)

        items = client.get("/api/v1/notices?keyword=人事", headers=h).json()["items"]
        assert len(items) >= 1
        assert "人事" in items[0]["title"]


# ===================================================================
# 场景 4：权限隔离
# ===================================================================
class TestE2E_PermissionIsolation:
    def test_user_cannot_create_doc(self, client, db, normal_user):
        vh = _auth(_login(client, "zhangsan", "user1234")["access_token"])
        resp = client.post("/api/v1/documents", json={
            "title": "越权", "effective_from": "2026-01-01",
        }, headers=vh)
        assert resp.status_code in (401, 403)

    def test_user_cannot_manage_users(self, client, db, normal_user):
        vh = _auth(_login(client, "zhangsan", "user1234")["access_token"])
        assert client.get("/api/v1/users", headers=vh).status_code in (401, 403)

    def test_user_cannot_access_recycle_bin(self, client, db, normal_user):
        vh = _auth(_login(client, "zhangsan", "user1234")["access_token"])
        assert client.get("/api/v1/recycle-bin", headers=vh).status_code in (401, 403)

    def test_user_cannot_view_audit_log(self, client, db, normal_user):
        vh = _auth(_login(client, "zhangsan", "user1234")["access_token"])
        assert client.get("/api/v1/audit-logs", headers=vh).status_code in (401, 403)

    def test_user_cannot_modify_others_notice(self, client, db, admin_user, normal_user):
        type_name = _ensure_notice_type(db)
        h = _auth(_login(client, "admin", "admin1234")["access_token"])
        resp = client.post("/api/v1/notices", json={
            "title": "管理员通知", "content": "c",
            "notice_type": type_name, "effective_at": "2026-01-01T00:00:00",
        }, headers=h)
        nid = resp.json()["id"]

        vh = _auth(_login(client, "zhangsan", "user1234")["access_token"])
        resp = client.put(f"/api/v1/notices/{nid}", json={"title": "篡改"}, headers=vh)
        assert resp.status_code in (401, 403)


# ===================================================================
# 场景 5：下载权限控制
# ===================================================================
class TestE2E_DownloadControl:
    """2026-11-04：allow_download 开关移除，下载权限 = 查看权限。"""

    def test_user_in_audience_can_download(self, client, db, admin_user, normal_user):
        h = _auth(_login(client, "admin", "admin1234")["access_token"])
        doc = _create_doc(client, h, title="下载控制", effective_from="2026-01-01")
        _upload_to_doc(client, h, doc["id"])

        vh = _auth(_login(client, "zhangsan", "user1234")["access_token"])
        # 默认 audience=all → 能看就能下
        resp = client.get(f"/api/v1/documents/{doc['id']}/file", headers=vh)
        assert resp.status_code == 200

    def test_user_outside_audience_cannot_download(
        self, client, db, admin_user, normal_user, user_factory
    ):
        other = user_factory("lisi", password="user1234", role="user")
        h = _auth(_login(client, "admin", "admin1234")["access_token"])
        doc = _create_doc(client, h, title="受限下载", effective_from="2026-01-01")
        _upload_to_doc(client, h, doc["id"])
        # 改成指定人员，名单里只有 lisi（不含 zhangsan）
        resp = client.put(f"/api/v1/documents/{doc['id']}",
                          json={"audience_scope": "user",
                                "audience_user_ids": [other.id]}, headers=h)
        assert resp.status_code == 200

        vh = _auth(_login(client, "zhangsan", "user1234")["access_token"])
        resp = client.get(f"/api/v1/documents/{doc['id']}/file", headers=vh)
        assert resp.status_code in (403, 404)

    def test_admin_always_can_download(self, client, db, admin_user):
        h = _auth(_login(client, "admin", "admin1234")["access_token"])
        doc = _create_doc(client, h, title="管理员下载", effective_from="2026-01-01")
        _upload_to_doc(client, h, doc["id"])
        resp = client.get(f"/api/v1/documents/{doc['id']}/file", headers=h)
        assert resp.status_code == 200


# ===================================================================
# 场景 6：审计日志
# ===================================================================
class TestE2E_AuditLog:
    def test_operations_logged(self, client, db, admin_user):
        h = _auth(_login(client, "admin", "admin1234")["access_token"])
        _create_doc(client, h, title="日志测试", effective_from="2026-01-01")

        resp = client.get("/api/v1/audit-logs", headers=h)
        assert resp.status_code == 200
        data = resp.json()
        items = data.get("items", data) if isinstance(data, dict) else data
        assert len(items) > 0
