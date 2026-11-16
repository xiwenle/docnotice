"""资料批量整理 + 修改密码/首登强制改密 测试。

覆盖：
- 整理视角筛选：uncategorized / untagged
- POST /api/v1/documents/bulk：批量设置分类 / 清除分类 / 追加标签（幂等）/ 批量归档
- 批量接口权限：普通用户 403；参数校验（无操作项、互斥参数）
- 登录响应 must_change_password；change-password 清除标记；/change-password 页面守卫
"""
from __future__ import annotations


# 与 tests/conftest.py 的默认测试密码保持一致
USER_PASSWORD = "user1234"


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _create_doc(client, admin_headers, title, category_id=None, tag_ids=None):
    resp = client.post(
        "/api/v1/documents",
        headers=admin_headers,
        json={
            "title": title,
            "effective_from": "2026-01-01",
            "category_id": category_id,
            "tag_ids": tag_ids or [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 整理视角筛选
# ---------------------------------------------------------------------------
def test_list_filter_uncategorized_and_untagged(client, admin_headers, category, tag):
    bare = _create_doc(client, admin_headers, "裸资料")
    organized = _create_doc(
        client, admin_headers, "已整理资料", category_id=category.id, tag_ids=[tag.id]
    )

    r = client.get("/api/v1/documents", headers=admin_headers, params={"uncategorized": "true"})
    ids = {d["id"] for d in r.json()["items"]}
    assert bare["id"] in ids and organized["id"] not in ids

    r = client.get("/api/v1/documents", headers=admin_headers, params={"untagged": "true"})
    ids = {d["id"] for d in r.json()["items"]}
    assert bare["id"] in ids and organized["id"] not in ids


# ---------------------------------------------------------------------------
# 批量整理
# ---------------------------------------------------------------------------
def test_bulk_set_and_clear_category(client, admin_headers, category_factory):
    d1 = _create_doc(client, admin_headers, "批量-1")
    d2 = _create_doc(client, admin_headers, "批量-2")
    cat = category_factory("批量目标分类")

    r = client.post(
        "/api/v1/documents/bulk",
        headers=admin_headers,
        json={"document_ids": [d1["id"], d2["id"]], "set_category_id": cat.id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 2

    got = client.get(f"/api/v1/documents/{d1['id']}", headers=admin_headers).json()
    assert got["category_id"] == cat.id

    # 清除分类
    r = client.post(
        "/api/v1/documents/bulk",
        headers=admin_headers,
        json={"document_ids": [d1["id"]], "clear_category": True},
    )
    assert r.status_code == 200
    got = client.get(f"/api/v1/documents/{d1['id']}", headers=admin_headers).json()
    assert got["category_id"] is None


def test_bulk_add_tag_idempotent(client, admin_headers, tag_factory):
    d = _create_doc(client, admin_headers, "批量标签")
    t = tag_factory("批量标签")

    for expected in (1, 0):  # 首次追加生效；重复执行幂等，不再重复追加
        r = client.post(
            "/api/v1/documents/bulk",
            headers=admin_headers,
            json={"document_ids": [d["id"]], "add_tag_id": t.id},
        )
        assert r.status_code == 200, r.text
        assert r.json()["updated"] == expected

    got = client.get(f"/api/v1/documents/{d['id']}", headers=admin_headers).json()
    assert got["tag_ids"].count(t.id) == 1



def test_bulk_archive_cannot_mix_category_or_tag(client, admin_headers, category):
    doc = client.post("/api/v1/documents", headers=admin_headers, json={
        "title": "互斥归档", "effective_from": "2026-01-01"
    }).json()
    r = client.post("/api/v1/documents/bulk", headers=admin_headers, json={
        "document_ids": [doc["id"]], "archive": True,
        "set_category_id": category.id,
    })
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
def test_bulk_archive(client, admin_headers):
    d = _create_doc(client, admin_headers, "批量删除")
    r = client.post(
        "/api/v1/documents/bulk",
        headers=admin_headers,
        json={"document_ids": [d["id"]], "archive": True, "reason": "整理测试"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["archived"] == 1
    # 再次批量删除同一条 → 跳过（已归档）
    r = client.post(
        "/api/v1/documents/bulk",
        headers=admin_headers,
        json={"document_ids": [d["id"]], "archive": True},
    )
    assert r.json()["archived"] == 0
    assert r.json()["skipped"][0]["id"] == d["id"]


def test_bulk_validation_and_permission(client, admin_headers, user_headers, category):
    # 无操作项 → 400
    r = client.post(
        "/api/v1/documents/bulk", headers=admin_headers, json={"document_ids": [1]}
    )
    assert r.status_code == 400
    # 互斥参数 → 400
    r = client.post(
        "/api/v1/documents/bulk",
        headers=admin_headers,
        json={"document_ids": [1], "set_category_id": category.id, "clear_category": True},
    )
    assert r.status_code == 400
    # 普通用户 → 403
    r = client.post(
        "/api/v1/documents/bulk",
        headers=user_headers,
        json={"document_ids": [1], "clear_category": True},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 修改密码 / 首登强制改密
# ---------------------------------------------------------------------------
def test_login_response_must_change_password_flag(client, user_factory):
    flagged = user_factory("flaguser", must_change_password=True)
    plain = user_factory("plainuser")

    r = client.post(
        "/api/v1/auth/login",
        json={"username": flagged.username, "password": USER_PASSWORD},
    )
    assert r.status_code == 200
    assert r.json()["must_change_password"] is True

    r = client.post(
        "/api/v1/auth/login",
        json={"username": plain.username, "password": USER_PASSWORD},
    )
    assert r.json()["must_change_password"] is False


def test_change_password_clears_flag(client, user_factory):
    user_factory("chpwuser", must_change_password=True)
    client.post(
        "/api/v1/auth/login",
        json={"username": "chpwuser", "password": USER_PASSWORD},
    )
    r = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": USER_PASSWORD, "new_password": "newPass123"},
    )
    assert r.status_code == 200, r.text
    me = client.get("/api/v1/auth/me").json()
    assert me["must_change_password"] is False
    # 新密码可登录
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "chpwuser", "password": "newPass123"},
    )
    assert r.status_code == 200


def test_change_password_page_guard(client, user_factory):
    # 匿名 → 302 登录页
    r = client.get("/change-password", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=%2Fchange-password"

    # 登录后 → 200
    user_factory("cppageuser")
    client.post(
        "/api/v1/auth/login",
        json={"username": "cppageuser", "password": USER_PASSWORD},
    )
    r = client.get("/change-password")
    assert r.status_code == 200
    assert "修改密码" in r.text
