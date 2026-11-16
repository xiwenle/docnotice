"""资料可见范围（2026-11-03 产品决策）。

与通知的「阅读对象」语义不同：
- 通知决定「谁必须读」（物化快照、算未读、算统计）
- 资料决定「谁可以看」（不产生任何已读记录）

覆盖：三种范围（全体/部门/用户）的列表过滤、搜索过滤、详情访问、
文件下载越权、管理员恒可见、存量默认全体可见、编辑切换范围。
"""
from __future__ import annotations

from helpers import login_headers, upload_document_file
from files.generators import make_pdf


def _mkdoc(client, headers, title, **kw):
    payload = {"title": title, "effective_from": "2026-01-01"}
    payload.update(kw)
    resp = client.post("/api/v1/documents", headers=headers, json=payload)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _titles(client, headers, **params):
    q = {"page_size": 50}
    q.update(params)
    return [d["title"] for d in client.get(
        "/api/v1/documents", headers=headers, params=q
    ).json()["items"]]


class TestScopeAll:
    def test_default_scope_is_all(self, client, admin_headers):
        doc = _mkdoc(client, admin_headers, "默认范围")
        assert doc["audience_scope"] == "all"
        assert doc["audience_label"] == "全体可见"

    def test_everyone_sees_all_scope(self, client, admin_headers, user_headers):
        _mkdoc(client, admin_headers, "全员可看")
        assert "全员可看" in _titles(client, user_headers)


class TestScopeUser:
    def test_only_selected_user_sees(self, client, admin_headers, user_factory):
        u1 = user_factory("指定甲", role="user", password="user1234")
        user_factory("无关乙", role="user", password="user1234")
        _mkdoc(client, admin_headers, "只给甲看",
               audience_scope="user", audience_user_ids=[u1.id])

        h1 = login_headers(client, "指定甲", "user1234")
        h2 = login_headers(client, "无关乙", "user1234")
        assert "只给甲看" in _titles(client, h1)
        assert "只给甲看" not in _titles(client, h2)

    def test_unselected_user_gets_404_on_detail(self, client, admin_headers, user_factory):
        u1 = user_factory("有权甲", role="user", password="user1234")
        user_factory("无权乙", role="user", password="user1234")
        doc = _mkdoc(client, admin_headers, "私有资料",
                     audience_scope="user", audience_user_ids=[u1.id])

        h2 = login_headers(client, "无权乙", "user1234")
        # 用 404 而非 403：不泄露"存在但你没权限"
        assert client.get(f"/api/v1/documents/{doc['id']}", headers=h2).status_code == 404

    def test_unselected_user_cannot_download(self, client, admin_headers, user_factory, tmp_path):
        """知道 id 也不能绕过列表直接取文件。"""
        u1 = user_factory("可下甲", role="user", password="user1234")
        user_factory("不可下乙", role="user", password="user1234")
        doc = _mkdoc(client, admin_headers, "受限文件",
                     audience_scope="user", audience_user_ids=[u1.id])
        pdf = make_pdf(tmp_path / "a.pdf")
        upload_document_file(client, admin_headers, doc["id"],
                             filename="a.pdf", content=pdf.read_bytes())

        h2 = login_headers(client, "不可下乙", "user1234")
        for path in ("file", "preview/pdf"):
            assert client.get(
                f"/api/v1/documents/{doc['id']}/{path}", headers=h2
            ).status_code == 404, f"{path} 应拒绝越权访问"

    def test_scope_user_requires_ids(self, client, admin_headers):
        resp = client.post("/api/v1/documents", headers=admin_headers, json={
            "title": "缺人员", "effective_from": "2026-01-01",
            "audience_scope": "user", "audience_user_ids": [],
        })
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_scope_user_rejects_admin_target(self, client, admin_headers, admin_user):
        resp = client.post("/api/v1/documents", headers=admin_headers, json={
            "title": "管理员不应作为目标", "effective_from": "2026-01-01",
            "audience_scope": "user", "audience_user_ids": [admin_user.id],
        })
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "管理员默认可见全部资料" in body["error"]["message"]


class TestScopeDepartment:
    def test_dept_member_sees_outsider_does_not(
        self, client, admin_headers, user_factory, department_factory
    ):
        dept = department_factory("受限部门")
        user_factory("部门内", role="user", password="user1234",
                     department_ids=[dept.id])
        user_factory("部门外", role="user", password="user1234")

        _mkdoc(client, admin_headers, "部门内部资料",
               audience_scope="department", audience_department_ids=[dept.id])

        inside = login_headers(client, "部门内", "user1234")
        outside = login_headers(client, "部门外", "user1234")
        assert "部门内部资料" in _titles(client, inside)
        assert "部门内部资料" not in _titles(client, outside)

    def test_scope_department_requires_ids(self, client, admin_headers):
        resp = client.post("/api/v1/documents", headers=admin_headers, json={
            "title": "缺部门", "effective_from": "2026-01-01",
            "audience_scope": "department", "audience_department_ids": [],
        })
        assert resp.status_code == 400


class TestAdminAlwaysSees:
    def test_admin_sees_restricted_document(self, client, admin_headers, user_factory):
        u = user_factory("某个用户", role="user", password="user1234")
        doc = _mkdoc(client, admin_headers, "管理员可见的私有资料",
                     audience_scope="user", audience_user_ids=[u.id])
        assert "管理员可见的私有资料" in _titles(client, admin_headers)
        assert client.get(
            f"/api/v1/documents/{doc['id']}", headers=admin_headers
        ).status_code == 200


class TestSearchAndFilters:
    def test_keyword_search_respects_scope(self, client, admin_headers, user_factory):
        """搜索不能成为越权入口。"""
        u1 = user_factory("搜索有权", role="user", password="user1234")
        user_factory("搜索无权", role="user", password="user1234")
        _mkdoc(client, admin_headers, "机密项目方案",
               audience_scope="user", audience_user_ids=[u1.id])

        h2 = login_headers(client, "搜索无权", "user1234")
        hits = client.get("/api/v1/documents", headers=h2,
                          params={"keyword": "机密"}).json()["items"]
        assert hits == []

    def test_list_totals_differ_by_user(self, client, admin_headers, user_factory):
        """分页 total 必须按可见性计算，不能把隐藏项算进总数。"""
        u1 = user_factory("计数有权", role="user", password="user1234")
        user_factory("计数无权", role="user", password="user1234")
        for i in range(3):
            _mkdoc(client, admin_headers, f"私有{i}",
                   audience_scope="user", audience_user_ids=[u1.id])
        _mkdoc(client, admin_headers, "公开资料")

        h1 = login_headers(client, "计数有权", "user1234")
        h2 = login_headers(client, "计数无权", "user1234")
        t1 = client.get("/api/v1/documents", headers=h1).json()["total"]
        t2 = client.get("/api/v1/documents", headers=h2).json()["total"]
        assert t1 == 4  # 3 私有 + 1 公开
        assert t2 == 1  # 只有公开


class TestEditScope:
    def test_switch_all_to_user(self, client, admin_headers, user_factory):
        u = user_factory("切换目标", role="user", password="user1234")
        doc = _mkdoc(client, admin_headers, "先公开后受限")

        resp = client.put(f"/api/v1/documents/{doc['id']}", headers=admin_headers,
                          json={"audience_scope": "user",
                                "audience_user_ids": [u.id]})
        assert resp.status_code == 200, resp.text
        detail = client.get(f"/api/v1/documents/{doc['id']}", headers=admin_headers).json()
        assert detail["audience_scope"] == "user"
        assert detail["audience_user_ids"] == [u.id]

    def test_switch_user_to_all_clears_targets(self, client, admin_headers, user_factory):
        u = user_factory("回收目标", role="user", password="user1234")
        doc = _mkdoc(client, admin_headers, "先受限后公开",
                     audience_scope="user", audience_user_ids=[u.id])

        resp = client.put(f"/api/v1/documents/{doc['id']}", headers=admin_headers,
                          json={"audience_scope": "all"})
        assert resp.status_code == 200, resp.text
        detail = client.get(f"/api/v1/documents/{doc['id']}", headers=admin_headers).json()
        assert detail["audience_scope"] == "all"
        assert detail["audience_user_ids"] == [], "切回全体应清空目标行"

        h = login_headers(client, "回收目标", "user1234")
        assert "先受限后公开" in _titles(client, h)
