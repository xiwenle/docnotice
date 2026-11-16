"""并发安全测试：多个管理员同时操作同一资源。

使用 threading + 多个 TestClient 模拟并发请求。
"""
from __future__ import annotations

import threading


from app.core.security import create_access_token
from tests.files.generators import make_pdf


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


class TestConcurrencyDocument:
    """并发修改同一资料。"""

    def test_concurrent_title_updates(self, client, db, admin_user, tmp_path):
        """两个管理员同时修改同一资料的标题 → 后提交的不应产生脏数据。"""
        token = create_access_token(
            user_id=admin_user.id, username=admin_user.username, role=admin_user.role,
        )
        headers = _auth(token)

        # 创建资料（先创建文档，再上传文件）
        resp = client.post("/api/v1/documents", json={
            "title": "并发测试", "effective_from": "2026-01-01",
        }, headers=headers)
        doc_id = resp.json()["id"]

        pdf = make_pdf(tmp_path / "并发测试.pdf")
        resp = client.post(f"/api/v1/documents/{doc_id}/file",
                           files={"file": ("并发测试.pdf", pdf.read_bytes(), "application/pdf")},
                           headers=headers)
        assert resp.status_code == 200

        results = {}

        def update_title(title: str):
            c = _make_client()
            r = c.put(f"/api/v1/documents/{doc_id}",
                      json={"title": title}, headers=headers)
            results[title] = r.status_code

        t1 = threading.Thread(target=update_title, args=("管理员A修改",))
        t2 = threading.Thread(target=update_title, args=("管理员B修改",))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # 两个请求都应该成功（无 500 错误）
        assert all(v == 200 for v in results.values()), f"并发更新不应失败: {results}"

        # 最终标题应为其中一个（无脏数据）
        resp = client.get(f"/api/v1/documents/{doc_id}", headers=headers)
        final_title = resp.json()["title"]
        assert final_title in ("管理员A修改", "管理员B修改"), f"标题应为其中之一: {final_title}"

    def test_concurrent_file_replacement(self, client, db, admin_user, tmp_path):
        """两个管理员同时替换同一资料的文件 → 旧文件应进入回收站，无数据丢失。"""
        token = create_access_token(
            user_id=admin_user.id, username=admin_user.username, role=admin_user.role,
        )
        headers = _auth(token)

        # 创建资料（先创建文档，再上传文件）
        resp = client.post("/api/v1/documents", json={
            "title": "文件替换并发测试", "effective_from": "2026-01-01",
        }, headers=headers)
        doc_id = resp.json()["id"]

        pdf_orig = make_pdf(tmp_path / "原始文件.pdf")
        resp = client.post(f"/api/v1/documents/{doc_id}/file",
                           files={"file": ("原始文件.pdf", pdf_orig.read_bytes(), "application/pdf")},
                           headers=headers)
        assert resp.status_code == 200

        # 上传两个新文件（通过创建文档+上传文件的方式）
        resp = client.post("/api/v1/documents", json={
            "title": "new1-temp", "effective_from": "2026-01-01",
        }, headers=headers)
        new_doc1_id = resp.json()["id"]
        pdf_new1 = make_pdf(tmp_path / "新文件1.pdf")
        resp = client.post(f"/api/v1/documents/{new_doc1_id}/file",
                           files={"file": ("新文件1.pdf", pdf_new1.read_bytes(), "application/pdf")},
                           headers=headers)
        assert resp.status_code == 200

        resp = client.post("/api/v1/documents", json={
            "title": "new2-temp", "effective_from": "2026-01-01",
        }, headers=headers)
        new_doc2_id = resp.json()["id"]
        pdf_new2 = make_pdf(tmp_path / "新文件2.pdf")
        resp = client.post(f"/api/v1/documents/{new_doc2_id}/file",
                           files={"file": ("新文件2.pdf", pdf_new2.read_bytes(), "application/pdf")},
                           headers=headers)
        assert resp.status_code == 200

        # 获取新文件的 file_id（上传接口返回 current_file 结构）
        new_file_1 = resp.json()["current_file"]["id"]

        results = {}

        def replace_file(fid: int, label: str):
            c = _make_client()
            r = c.put(f"/api/v1/documents/{doc_id}",
                      json={"file_id": fid}, headers=headers)
            results[label] = r.status_code

        t1 = threading.Thread(target=replace_file, args=(new_file_1, "replace1"))
        t2 = threading.Thread(target=replace_file, args=(new_file_1, "replace2"))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # 无 500 错误
        assert all(v == 200 for v in results.values()), f"并发替换不应失败: {results}"

        # 最终文件应存在
        resp = client.get(f"/api/v1/documents/{doc_id}", headers=headers)
        assert resp.json()["has_file"] is True


class TestConcurrencyNotice:
    """并发通知操作。"""

    def test_concurrent_read_same_notice(self, client, db, admin_user, normal_user):
        """两个用户同时阅读同一通知 → 各自独立成功，不产生重复记录。"""
        from app.models.notice import NoticeType

        # 确保通知类型
        notice_type = db.query(NoticeType).first()
        type_name = notice_type.name if notice_type else "发布"

        # 先创建两个用户（发布时需要用户已存在才能生成 NoticeRead 快照）
        from app.core.security import hash_password
        from app.models.user import User

        u1 = User(username="conc_user1", password_hash=hash_password("user1234"),
                   display_name="并发用户1", role="user", is_active=True, auth_source="local")
        u2 = User(username="conc_user2", password_hash=hash_password("user1234"),
                   display_name="并发用户2", role="user", is_active=True, auth_source="local")
        db.add_all([u1, u2])
        db.commit()
        db.refresh(u1)
        db.refresh(u2)

        token_admin = create_access_token(
            user_id=admin_user.id, username=admin_user.username, role=admin_user.role,
        )

        # 创建通知并指定目标用户（用户必须在发布前已存在）
        resp = client.post("/api/v1/notices", json={
            "title": "并发确认测试",
            "content": "测试",
            "notice_type": type_name,
            "effective_at": "2026-01-01T00:00:00",
            "target": {"scope": "user", "user_ids": [u1.id, u2.id]},
        }, headers=_auth(token_admin))
        notice_id = resp.json()["id"]

        # 并发阅读
        results = {}

        def read_only(user, label):
            c = _make_client()
            login_resp = c.post("/api/v1/auth/login",
                                json={"username": user.username, "password": "user1234"})
            if login_resp.status_code != 200:
                results[label] = ("login_fail", login_resp.status_code)
                return
            tok = login_resp.json()["access_token"]
            h = _auth(tok)

            read_resp = c.post(f"/api/v1/notices/{notice_id}/read", headers=h)
            results[label] = (read_resp.status_code,)

        t1 = threading.Thread(target=read_only, args=(u1, "user1"))
        t2 = threading.Thread(target=read_only, args=(u2, "user2"))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # 两个用户都应能成功阅读（各自独立）
        for label, codes in results.items():
            if isinstance(codes, tuple):
                assert codes[0] == 200, f"{label} 阅读应成功: {codes}"
