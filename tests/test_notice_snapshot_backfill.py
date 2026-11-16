"""阅读快照补建（backfill）回归测试。

修复的问题：通知**发布之后**才创建/启用/调入部门的用户，因发布时物化的
NoticeRead 快照不含他们，永远看不到"全体用户/本部门"通知，阅读统计漏计。

三道防线（互为冗余兜底）：
1. 创建用户时补建（user_service.create_user）
2. 停用→启用 / 调入阅读部门时补建（user_service.update_user）
3. 登录时补建（auth_service.login）
兜底：通知列表/详情访问时惰性补建（notice_service._ensure_snapshot /
backfill_notice_reads）——覆盖持有效旧 Token 直接访问（无登录事件）的场景。
"""
from __future__ import annotations

from app.core.security import create_access_token
from helpers import bearer_headers, create_document


def _create_notice(client, headers, **kw):
    payload = {
        "title": kw.get("title", "快照补建通知"),
        "notice_type": kw.get("notice_type", "发布"),
        "content": kw.get("content", "正文"),
    }
    if kw.get("target") is not None:
        payload["target"] = kw["target"]
    if kw.get("document_ids"):
        payload["document_ids"] = kw["document_ids"]
    resp = client.post("/api/v1/notices", headers=headers, json=payload)
    # 2026-11-04：创建即发布，需有真实阅读对象；无读者时补一个再重试
    if resp.status_code == 400 and "阅读对象范围为空" in resp.text:
        client.post("/api/v1/users", headers=headers, json={
            "username": "auto_reader", "password": "user1234",
            "display_name": "自动补建读者", "role": "user",
        })
        resp = client.post("/api/v1/notices", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _ensure_reader(client, headers) -> None:
    """保证存在一个普通用户。

    2026-11-03：管理员不进阅读对象，「全体」范围只有管理员时发布会被拒。
    本文件关心快照补建，不关心具体谁读，统一补一个读者。
    """
    client.post("/api/v1/users", headers=headers, json={
        "username": "backfill_reader", "password": "user1234",
        "display_name": "补建读者", "role": "user",
    })


class _OkResp:
    """2026-11-04：创建即发布，/publish 已移除；保留占位以兼容旧断言。"""

    status_code = 200

    def __init__(self, body=None):
        self._body = body or {}

    def json(self):
        return self._body


def _publish(client, headers, notice_id, **kw):
    detail = client.get(f"/api/v1/notices/{notice_id}", headers=headers)
    if detail.status_code != 200:
        return detail
    return _OkResp(detail.json())




def _stats(client, headers, notice_id):
    return client.get(
        f"/api/v1/notices/{notice_id}/statistics", headers=headers
    ).json()


class TestBackfillOnCreateAndLogin:
    def test_user_created_after_publish_sees_all_notice_on_login(
        self, client, admin_headers, admin_user, user_factory, headers_for
    ):
        """核心场景（用户实测）：发布后创建的用户登录即能看到全体通知。"""
        notice = _create_notice(client, admin_headers, title="全体通知A")
        _publish(client, admin_headers, notice["id"])
        # 发布之后才创建用户（ORM 直插，不走补建）
        late_user = user_factory("后建用户", role="user")
        # 登录（第一道防线之一：login 补建）→ 列表立即可见
        h = headers_for(late_user)
        items = client.get("/api/v1/notices", headers=h).json()
        assert notice["id"] in [n["id"] for n in items["items"]]
        # 工作台计数与列表一致
        dash = client.get("/api/v1/dashboard", headers=h).json()
        assert dash["notice_count"] == 1
        assert dash["my_unread_notices"] == 1
        # 阅读统计不再漏计：该用户必须出现在应读名单里
        audience = client.get(
            f"/api/v1/notices/{notice['id']}/audience", headers=admin_headers
        ).json()
        assert any(u["username"] == late_user.username for u in audience["items"]), (
            f"后建用户未补建进名单：{[u['username'] for u in audience['items']]}"
        )

    def test_user_created_via_api_backfilled_immediately(
        self, client, admin_headers, admin_user
    ):
        """第一道防线：API 创建用户时即补建（无需等待登录）。"""
        notice = _create_notice(client, admin_headers, title="全体通知B")
        _publish(client, admin_headers, notice["id"])
        resp = client.post(
            "/api/v1/users",
            headers=admin_headers,
            json={"username": "api新用户", "password": "user1234",
                  "display_name": "API新用户", "role": "user",
                  "must_change_password": False},
        )
        assert resp.status_code in (200, 201), resp.text
        uid = resp.json()["id"]
        # 直接签发 Token（绕过登录事件）即可见 → 证明创建时已补建
        from app.core.security import create_access_token as _cat

        h = bearer_headers(_cat(user_id=uid, username="api新用户", role="user"))
        items = client.get("/api/v1/notices", headers=h).json()
        assert notice["id"] in [n["id"] for n in items["items"]]

    def test_backfill_is_idempotent(self, client, admin_headers, admin_user,
                                    user_factory, headers_for):
        notice = _create_notice(client, admin_headers, title="幂等通知")
        _publish(client, admin_headers, notice["id"])
        u = user_factory("幂等用户", role="user")
        h = headers_for(u)
        first = _stats(client, admin_headers, notice["id"])["target_user_count"]
        # 重复登录（重复补建）不产生重复快照
        client.post("/api/v1/auth/login", json={"username": "幂等用户",
                                                "password": "user1234"})
        client.get("/api/v1/notices", headers=h)
        again = _stats(client, admin_headers, notice["id"])["target_user_count"]
        assert first == again, f"重复补建不得改变分母：{first} → {again}"
        assert again >= 1, "至少包含该用户"

    def test_non_target_user_still_forbidden(self, client, admin_headers,
                                             admin_user, user_factory, headers_for):
        """惰性补建不得越权：非阅读对象仍 403。"""
        u1 = user_factory("定向甲", role="user")
        u2 = user_factory("无关乙", role="user")
        notice = _create_notice(
            client, admin_headers,
            target={"scope": "user", "user_ids": [u1.id]},
        )
        _publish(client, admin_headers, notice["id"])
        resp = client.get(
            f"/api/v1/notices/{notice['id']}", headers=headers_for(u2)
        )
        assert resp.status_code == 403
        items = client.get("/api/v1/notices", headers=headers_for(u2)).json()
        assert items["total"] == 0


class TestBackfillOnUpdate:
    def test_reactivated_user_sees_notice(
        self, client, admin_headers, admin_user, user_factory, headers_for
    ):
        """发布时停用的用户：启用后立即可见（update_user 触发补建）。"""
        u = user_factory("休眠用户", role="user", is_active=False)
        notice = _create_notice(client, admin_headers, title="复职通知")
        _publish(client, admin_headers, notice["id"])  # 发布时 u 停用 → 无快照
        resp = client.put(
            f"/api/v1/users/{u.id}", headers=admin_headers, json={"is_active": True}
        )
        assert resp.status_code == 200
        h = headers_for(u)
        items = client.get("/api/v1/notices", headers=h).json()
        assert notice["id"] in [n["id"] for n in items["items"]]
        audience = client.get(
            f"/api/v1/notices/{notice['id']}/audience", headers=admin_headers
        ).json()
        assert any(row["username"] == u.username for row in audience["items"]), (
            "复职用户应补建进应读名单"
        )

    def test_department_move_user_sees_dept_notice(
        self, client, admin_headers, admin_user, user_factory,
        department_factory, headers_for
    ):
        """发布后调入阅读部门的用户：换部门后立即可见部门通知。"""
        dept = department_factory("新设部门")
        user_factory("部门种子", role="user", department_ids=[dept.id])
        u = user_factory("调岗用户", role="user")
        notice = _create_notice(
            client, admin_headers,
            target={"scope": "department", "department_ids": [dept.id]},
        )
        _publish(client, admin_headers, notice["id"])  # 发布时 u 不在部门 → 无快照
        # 调入部门（触发 update_user 补建）
        resp = client.put(
            f"/api/v1/users/{u.id}",
            headers=admin_headers,
            json={"department_ids": [dept.id]},
        )
        assert resp.status_code == 200
        h = headers_for(u)
        items = client.get("/api/v1/notices", headers=h).json()
        assert notice["id"] in [n["id"] for n in items["items"]]
        # 统计：种子 + 调入者（均为普通用户；管理员不进分母）
        assert _stats(client, admin_headers, notice["id"])["target_user_count"] == 2


class TestLazyEnsureSnapshot:
    def test_detail_access_backfills_without_login_event(
        self, client, admin_headers, admin_user, user_factory
    ):
        """兜底防线：持有效 Token（无登录事件）直接访问详情 → 惰性补建。"""
        notice = _create_notice(client, admin_headers, title="惰性补建通知")
        _publish(client, admin_headers, notice["id"])
        u = user_factory("直访用户", role="user")
        h = bearer_headers(
            create_access_token(user_id=u.id, username=u.username, role=u.role)
        )
        resp = client.get(f"/api/v1/notices/{notice['id']}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["my_read_status"] == "unread"
        # 快照已补建 → 名单包含该用户
        audience = client.get(
            f"/api/v1/notices/{notice['id']}/audience", headers=admin_headers
        ).json()
        assert any(a["username"] == u.username for a in audience["items"]), (
            "惰性补建后该用户应出现在应读名单"
        )

    def test_link_notice_document_roundtrip_still_consistent(
        self, client, admin_headers, admin_user, user_factory, headers_for
    ):
        """与上一轮逻辑修复联动：发布后新用户能看到关联资料的通知，
        且已删除资料不会回流到关联列表。"""
        doc = create_document(client, admin_headers, title="补建联动资料")
        notice = _create_notice(
            client, admin_headers, title="关联通知补建", document_ids=[doc["id"]]
        )
        _publish(client, admin_headers, notice["id"])
        u = user_factory("联动用户", role="user")
        h = headers_for(u)
        assert notice["id"] in [
            n["id"] for n in client.get("/api/v1/notices", headers=h).json()["items"]
        ]
        # 删除资料后，通知详情对普通用户不再显示该资料（上一轮修复不被回归）
        resp = client.delete(f"/api/v1/documents/{doc['id']}", headers=admin_headers)
        assert resp.status_code == 200
        linked = client.get(
            f"/api/v1/notices/{notice['id']}/documents", headers=h
        ).json()["items"]
        assert doc["id"] not in [d["id"] for d in linked]
