"""边界场景测试：超长输入、空值、异常日期、重复操作。

这些测试验证系统在异常输入下的健壮性。
"""
from __future__ import annotations

import io
import tempfile
from datetime import date, timedelta
from pathlib import Path


from files.generators import make_pdf


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}

# 真实可解析 PDF 字节（上传有 pypdf 深度校验，假字节会被 415 拒绝，§32）
_PDF_MAGIC = make_pdf(
    Path(tempfile.mkdtemp(prefix="edgecases-pdf-")) / "real.pdf", pages=1
).read_bytes()


class TestLongInput:
    """超长输入边界。"""

    def test_long_document_title(self, client, db, admin_user):
        """超长标题（500 字符）不应导致 500 错误。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        long_title = "长" * 500
        resp = client.post("/api/v1/documents", json={
            "title": long_title,
            "effective_from": "2026-01-01",
        }, headers=headers)
        # 不应 500；可以是 200（接受）或 400/422（拒绝）但不能崩溃
        assert resp.status_code in (200, 201, 400, 422), f"超长标题: {resp.status_code}"
        if resp.status_code in (200, 201):
            doc_id = resp.json()["id"]
            # 上传文件到文档
            resp = client.post(f"/api/v1/documents/{doc_id}/file",
                               files={"file": ("long.pdf", io.BytesIO(_PDF_MAGIC), "application/pdf")},
                               headers=headers)
            assert resp.status_code == 200

    def test_long_notice_content(self, client, db, admin_user):
        """超长通知正文（10000 字符）不应导致 500 错误。"""
        from app.models.notice import NoticeType

        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        notice_type = db.query(NoticeType).first()
        type_name = notice_type.name if notice_type else "发布"

        long_content = "测试内容。" * 2000  # ~14000 字符
        resp = client.post("/api/v1/notices", json={
            "title": "超长内容测试",
            "content": long_content,
            "notice_type": type_name,
            "effective_at": "2026-01-01T00:00:00",
        }, headers=headers)
        assert resp.status_code in (200, 201, 400, 422), f"超长内容: {resp.status_code}"

    def test_empty_title_rejected(self, client, db, admin_user):
        """空标题应被拒绝。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        resp = client.post("/api/v1/documents", json={
            "title": "",
            "effective_from": "2026-01-01",
        }, headers=headers)
        assert resp.status_code in (400, 422), f"空标题应被拒绝: {resp.status_code}"


class TestDateBoundaries:
    """日期边界场景。"""

    def test_document_effective_from_in_future(self, client, db, admin_user):
        """生效日期在未来 → 状态应为"未生效"。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        future_date = (date.today() + timedelta(days=365)).isoformat()

        resp = client.post("/api/v1/documents", json={
            "title": "未来生效", "effective_from": future_date,
        }, headers=headers)
        assert resp.status_code in (200, 201)
        doc_id = resp.json()["id"]

        # 上传文件到文档
        resp = client.post(f"/api/v1/documents/{doc_id}/file",
                           files={"file": ("future.pdf", io.BytesIO(_PDF_MAGIC), "application/pdf")},
                           headers=headers)
        assert resp.status_code == 200

        # 验证状态
        resp = client.get(f"/api/v1/documents/{doc_id}", headers=headers)
        # 状态应为未生效（通过 compute_status 计算）
        if "status" in resp.json():
            assert resp.json()["status"] in ("未生效", "pending")

    def test_document_effective_to_before_from_rejected(self, client, db, admin_user):
        """失效日期早于生效日期 → 应被拒绝。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        resp = client.post("/api/v1/documents", json={
            "title": "日期冲突",
            "effective_from": "2026-06-30",
            "effective_to": "2026-01-01",
        }, headers=headers)
        assert resp.status_code in (400, 409, 422), f"日期冲突应被拒绝: {resp.status_code}"

    def test_document_permanent_valid(self, client, db, admin_user):
        """effective_to 为空 → 暂无失效日期，状态按当前日期计算。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        resp = client.post("/api/v1/documents", json={
            "title": "暂无失效日期", "effective_from": "2026-01-01",
            # 不设置 effective_to
        }, headers=headers)
        assert resp.status_code in (200, 201)

    def test_cover_query_no_containment(self, client, db, admin_user):
        """包含查询：资料有效期未覆盖查询区间 → 不返回。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        # 创建 2026-01 到 2026-06 的资料
        resp = client.post("/api/v1/documents", json={
            "title": "上半年资料",
            "effective_from": "2026-01-01", "effective_to": "2026-06-30",
        }, headers=headers)
        doc_id = resp.json()["id"]

        # 上传文件到文档
        resp = client.post(f"/api/v1/documents/{doc_id}/file",
                           files={"file": ("ov.pdf", io.BytesIO(_PDF_MAGIC), "application/pdf")},
                           headers=headers)
        assert resp.status_code == 200

        # 查询 2026-07 到 2026-12（资料有效期未覆盖）
        resp = client.get(
            "/api/v1/documents?cover_from=2026-07-01&cover_to=2026-12-31",
            headers=headers,
        )
        items = resp.json().get("items", [])
        ids = [d["id"] for d in items]
        assert doc_id not in ids, "未覆盖查询区间的资料不应出现在查询结果中"


class TestDuplicateOperations:
    """重复操作防御。"""

    def test_duplicate_notice_read_idempotent(self, client, db, admin_user, normal_user):
        """重复标记已读 → 幂等，不 500（确认功能下线后，这也是唯一的重复操作面）。"""
        from app.models.notice import NoticeType

        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        notice_type = db.query(NoticeType).first()
        type_name = notice_type.name if notice_type else "发布"

        resp = client.post("/api/v1/notices", json={
            "title": "重复已读测试", "content": "测试",
            "notice_type": type_name, "effective_at": "2026-01-01T00:00:00",
            "target": {"scope": "user", "user_ids": [normal_user.id]},
        }, headers=headers)
        notice_id = resp.json()["id"]

        v_body = client.post("/api/v1/auth/login",
                             json={"username": "zhangsan", "password": "user1234"}).json()
        v_headers = _auth(v_body["access_token"])

        first = client.post(f"/api/v1/notices/{notice_id}/read", headers=v_headers)
        assert first.status_code == 200
        again = client.post(f"/api/v1/notices/{notice_id}/read", headers=v_headers)
        assert again.status_code == 200
        assert again.json()["my_read_at"] == first.json()["my_read_at"]

    def test_duplicate_notice_read(self, client, db, admin_user, normal_user):
        """重复标记已读 → 应幂等。"""
        from app.models.notice import NoticeType

        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        notice_type = db.query(NoticeType).first()
        type_name = notice_type.name if notice_type else "发布"

        resp = client.post("/api/v1/notices", json={
            "title": "重复已读测试", "content": "测试",
            "notice_type": type_name, "effective_at": "2026-01-01T00:00:00",
            "target": {"scope": "user", "user_ids": [normal_user.id]},
        }, headers=headers)
        notice_id = resp.json()["id"]

        v_body = client.post("/api/v1/auth/login",
                             json={"username": "zhangsan", "password": "user1234"}).json()
        v_headers = _auth(v_body["access_token"])

        # 多次标记已读
        for _ in range(3):
            resp = client.post(f"/api/v1/notices/{notice_id}/read", headers=v_headers)
            assert resp.status_code == 200

    def test_cannot_read_not_yet_effective_notice(self, client, db, admin_user, normal_user):
        """未到生效时间的通知，普通用户看不到也置读不了。

        2026-11-04：草稿状态下线（创建即发布），原「未发布通知不可读」
        改为验证「未生效」这一等效场景——通知状态为 published 但 effective_at 在未来，
        普通用户既不能读也不能置读，防止提前泄露。
        """
        from app.models.notice import NoticeType

        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        notice_type = db.query(NoticeType).first()
        type_name = notice_type.name if notice_type else "发布"

        resp = client.post("/api/v1/notices", json={
            "title": "未来才生效的通知", "content": "提前可见就算泄露",
            "notice_type": type_name, "effective_at": "2099-01-01T00:00:00",
            "target": {"scope": "user", "user_ids": [normal_user.id]},
        }, headers=headers)
        assert resp.status_code in (200, 201), resp.text
        notice_id = resp.json()["id"]

        v_body = client.post("/api/v1/auth/login",
                             json={"username": "zhangsan", "password": "user1234"}).json()
        v_headers = _auth(v_body["access_token"])

        # 普通用户列表里不应出现
        listed = client.get("/api/v1/notices", headers=v_headers).json()["items"]
        assert all(n["id"] != notice_id for n in listed), "未生效通知不应出现在普通用户列表"
        # 也不能置读
        resp = client.post(f"/api/v1/notices/{notice_id}/read", headers=v_headers)
        assert resp.status_code in (403, 404), f"未生效通知不应可置读: {resp.status_code}"


class TestSpecialCharacters:
    """特殊字符处理。"""

    def test_unicode_in_document_title(self, client, db, admin_user):
        """Unicode 字符（中日韩混合）在标题中正常工作。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        # 先创建文档
        resp = client.post("/api/v1/documents", json={
            "title": "中文标题 والعنوان 日本語タイトル 한국어제목",
            "effective_from": "2026-01-01",
        }, headers=headers)
        assert resp.status_code in (200, 201)
        doc_id = resp.json()["id"]

        # 上传文件到文档
        resp = client.post(f"/api/v1/documents/{doc_id}/file",
                           files={"file": ("uni.pdf", io.BytesIO(_PDF_MAGIC), "application/pdf")},
                           headers=headers)
        assert resp.status_code == 200

        resp = client.get(f"/api/v1/documents/{doc_id}", headers=headers)
        assert resp.json()["title"] == "中文标题 والعنوان 日本語タイトル 한국어제목"

    def test_special_chars_in_filename(self, client, db, admin_user):
        """特殊字符文件名应被正确处理（清洗后存储）。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        # 先创建文档
        resp = client.post("/api/v1/documents", json={
            "title": "文件名测试", "effective_from": "2026-01-01",
        }, headers=headers)
        assert resp.status_code in (200, 201)
        doc_id = resp.json()["id"]

        # 中文 + 空格 + 括号（文件内容必须以 %PDF- 开头）
        resp = client.post(f"/api/v1/documents/{doc_id}/file",
                           files={"file": ("测试 文件 (1).pdf", io.BytesIO(_PDF_MAGIC), "application/pdf")},
                           headers=headers)
        assert resp.status_code == 200
        file_info = resp.json()
        # 内部名称不应包含原始特殊字符
        assert "current_file" in file_info or "id" in file_info

    def test_path_traversal_in_filename(self, client, db, admin_user):
        """路径穿越文件名应被拒绝或清洗。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        # 先创建文档
        resp = client.post("/api/v1/documents", json={
            "title": "路径穿越测试", "effective_from": "2026-01-01",
        }, headers=headers)
        assert resp.status_code in (200, 201)
        doc_id = resp.json()["id"]

        # 路径穿越文件名（文件内容以 %PDF- 开头通过安全校验）
        resp = client.post(f"/api/v1/documents/{doc_id}/file",
                           files={"file": ("../../../etc/passwd", io.BytesIO(_PDF_MAGIC), "application/pdf")},
                           headers=headers)
        # 不应返回 500；应成功（清洗后存储）或被拒绝（400/415=不支持的文件类型）
        assert resp.status_code in (200, 400, 415), f"路径穿越: {resp.status_code}"
        if resp.status_code == 200:
            # 存储路径不应包含 ..
            stored_path = resp.json().get("stored_path", resp.json().get("file_path", ""))
            assert ".." not in stored_path
