"""整体逻辑一致性回归测试（整体逻辑优化轮）。

覆盖「改来改去」造成的口径不一致问题，规则统一为：
1. 已删除（cancelled）通知不进入任何业务列表（资料关联通知、最近通知等），
   统一只在回收站出现；
2. 草稿通知仅 admin 可见（不向普通用户泄露未发布内容）；
3. 已删除（回收站中）资料不作为关联资料展示/不可再被关联；
4. 工作台「我的未读」与通知列表 unread_only 口径严格一致
   （普通用户只统计自己是阅读对象的通知）；
5. 回收站恢复通知：草稿恢复为草稿（不绕过快照物化）；已发布的废止/替换
   通知恢复时重新联动资料失效（与发布语义对称）；
6. 回收站恢复资料后业务列表缓存立即失效；
7. 停用的通知类型/已删除的分类不能用于新建；
8. 对用户展示的数字 = 点进去看到的条数：工作台「30天内失效」卡 ↔ status=expiring、
   标签 document_count ↔ 按标签筛选、阅读统计分母 ↔ 点名册 ↔ 列表率条（只算启用用户）；
9. 操作日志按日期筛选时，结束日期为纯日期即含当日全天。
"""
from __future__ import annotations

from datetime import date, timedelta


from helpers import create_document, login_headers


def _create_notice(client, headers, **kw):
    payload = {
        "title": kw.get("title", "一致性测试通知"),
        "notice_type": kw.get("notice_type", "发布"),
    }
    if kw.get("content") is not None:
        payload["content"] = kw["content"]
    if kw.get("document_ids"):
        payload["document_ids"] = kw["document_ids"]
    if kw.get("target") is not None:
        payload["target"] = kw["target"]
    if kw.get("effective_at"):
        payload["effective_at"] = kw["effective_at"]
    resp = client.post("/api/v1/notices", headers=headers, json=payload)
    # 2026-11-04：创建即发布，需有真实阅读对象；无读者时补一个再重试
    if resp.status_code == 400 and "阅读对象范围为空" in resp.text:
        client.post("/api/v1/users", headers=headers, json={
            "username": "auto_reader", "password": "user1234",
            "display_name": "自动补建读者", "role": "user",
        })
        resp = client.post("/api/v1/notices", headers=headers, json=payload)
    assert resp.status_code == 201, f"创建通知失败 {resp.status_code}: {resp.text}"
    return resp.json()


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




def _cancel(client, headers, notice_id):
    resp = client.post(
        f"/api/v1/notices/{notice_id}/cancel", headers=headers, json={}
    )
    assert resp.status_code == 200, f"删除失败 {resp.status_code}: {resp.text}"
    return resp.json()


def _doc_detail(client, headers, doc_id):
    resp = client.get(f"/api/v1/documents/{doc_id}", headers=headers)
    assert resp.status_code == 200, f"资料详情失败 {resp.status_code}: {resp.text}"
    return resp.json()


class TestLinkedNoticesVisibility:
    """资料详情「关联通知」：已删除通知不出现；草稿仅超管可见。"""

    def test_cancelled_notice_hidden_from_document_detail(
        self, client, admin_headers, user_headers, admin_user, normal_user
    ):
        doc = create_document(client, admin_headers, title="关联通知资料A")
        notice = _create_notice(
            client, admin_headers, content="正文", document_ids=[doc["id"]]
        )
        _publish(client, admin_headers, notice["id"])
        # 普通用户先能看到
        assert _doc_detail(client, user_headers, doc["id"])["linked_notices"], "发布后应可见"
        # 删除通知（进回收站）
        _cancel(client, admin_headers, notice["id"])
        # 普通用户与超管的资料详情都不再显示已删除通知（用户点名的场景）
        for headers in (user_headers, admin_headers):
            linked = _doc_detail(client, headers, doc["id"])["linked_notices"]
            assert notice["id"] not in [n["id"] for n in linked]
        # 独立关联通知接口口径一致
        items = client.get(
            f"/api/v1/documents/{doc['id']}/notices", headers=user_headers
        ).json()["items"]
        assert notice["id"] not in [n["id"] for n in items]

    def test_not_yet_effective_notice_hidden_from_normal_user(
        self, client, admin_headers, user_headers, admin_user, normal_user
    ):
        """未到生效时间的通知，普通用户在资料详情里看不到；管理员可见。

        2026-11-04：草稿下线，这里改用「已发布但未来生效」这一等效场景。
        """
        doc = create_document(client, admin_headers, title="未来生效资料B")
        future = _create_notice(
            client, admin_headers, title="未来才生效", content="x",
            document_ids=[doc["id"]], effective_at="2099-01-01T00:00:00",
        )
        linked = _doc_detail(client, user_headers, doc["id"])["linked_notices"]
        assert future["id"] not in [n["id"] for n in linked], "未生效通知不得提前泄露"
        linked_admin = _doc_detail(client, admin_headers, doc["id"])["linked_notices"]
        assert future["id"] in [n["id"] for n in linked_admin]


class TestArchivedDocumentInNotice:
    """通知侧：回收站资料不展示、不可关联。"""

    def _doc_with_notice(self, client, admin_headers, normal_user):
        doc = create_document(client, admin_headers, title="将被删除的资料C")
        notice = _create_notice(
            client, admin_headers, content="正文", document_ids=[doc["id"]]
        )
        _publish(client, admin_headers, notice["id"])
        resp = client.delete(f"/api/v1/documents/{doc['id']}", headers=admin_headers)
        assert resp.status_code == 200
        return doc, notice

    def test_notice_detail_hides_archived_doc_for_normal_user(
        self, client, admin_headers, user_headers, admin_user, normal_user
    ):
        doc, notice = self._doc_with_notice(client, admin_headers, normal_user)
        detail = client.get(
            f"/api/v1/notices/{notice['id']}", headers=user_headers
        ).json()
        assert doc["id"] not in [d["id"] for d in detail["documents"]]
        linked = client.get(
            f"/api/v1/notices/{notice['id']}/documents", headers=user_headers
        ).json()["items"]
        assert doc["id"] not in [d["id"] for d in linked]
        # admin 保留全量视角（状态为 archived）
        linked_admin = client.get(
            f"/api/v1/notices/{notice['id']}/documents", headers=admin_headers
        ).json()["items"]
        assert doc["id"] in [d["id"] for d in linked_admin]

    def test_cannot_link_archived_document(
        self, client, admin_headers, user_headers, admin_user, normal_user
    ):
        doc, notice = self._doc_with_notice(client, admin_headers, normal_user)
        # 新建通知关联已删除资料 → 400 且提示明确
        resp = client.post(
            "/api/v1/notices",
            headers=admin_headers,
            json={"title": "关联已删资料", "content": "x",
                  "document_ids": [doc["id"]]},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "回收站" in body["error"]["message"]
        # 修改既有通知为已删除资料关联 → 同样阻止
        resp = client.put(
            f"/api/v1/notices/{notice['id']}",
            headers=admin_headers,
            json={"document_ids": [doc["id"]]},
        )
        assert resp.status_code == 400


class TestDashboardUnreadConsistency:
    """工作台「我的未读」与通知列表 unread_only 口径一致。"""

    def test_unread_counts_only_targeted_users(
        self, client, admin_headers, user_factory, headers_for, admin_user
    ):
        u1 = user_factory("对象甲", role="user")
        u2 = user_factory("路人乙", role="user")
        notice = _create_notice(
            client, admin_headers, content="只发给甲",
            target={"scope": "user", "user_ids": [u1.id]},
        )
        _publish(client, admin_headers, notice["id"])

        # 阅读对象甲：工作台未读=1，且与 unread_only 列表一致
        dash1 = client.get("/api/v1/dashboard", headers=headers_for(u1)).json()
        assert dash1["my_unread_notices"] == 1
        assert dash1["notice_count"] == 1
        unread1 = client.get(
            "/api/v1/notices", headers=headers_for(u1)
        ).json()
        # 注意：该请求不带 unread_only 参数，仅校验列表可见性
        assert unread1["total"] == 1

        # 非阅读对象乙：修复前会把全公司通知计成自己的未读（口径不一致）
        dash2 = client.get("/api/v1/dashboard", headers=headers_for(u2)).json()
        assert dash2["my_unread_notices"] == 0
        assert dash2["notice_count"] == 0

    def test_unread_matches_unread_only_list(
        self, client, admin_headers, user_factory, headers_for, admin_user, normal_user
    ):
        notice = _create_notice(client, admin_headers, content="全员")
        _publish(client, admin_headers, notice["id"])
        h = headers_for(normal_user)
        dash = client.get("/api/v1/dashboard", headers=h).json()
        unread_list = client.get(
            "/api/v1/notices", headers=h, params={"unread_only": True}
        ).json()
        assert dash["my_unread_notices"] == unread_list["total"] == 1
        # 标记已读后，两边同步归零
        client.post(f"/api/v1/notices/{notice['id']}/read", headers=h, json={})
        dash = client.get("/api/v1/dashboard", headers=h).json()
        unread_list = client.get(
            "/api/v1/notices", headers=h, params={"unread_only": True}
        ).json()
        assert dash["my_unread_notices"] == unread_list["total"] == 0


class TestNoticeRestore:
    """回收站恢复通知：草稿归草稿；废止联动对称回滚。"""

    def test_restore_deleted_notice_returns_to_published(
        self, client, admin_headers, user_headers, admin_user, normal_user
    ):
        """删除 → 恢复：回到 published，阅读快照与统计保持。

        2026-11-04：草稿下线，恢复不再有「恢复到草稿」分支。
        """
        note = _create_notice(client, admin_headers, title="被删通知", content="x")
        resp = client.post(f"/api/v1/notices/{note['id']}/cancel",
                           headers=admin_headers, json={})
        assert resp.status_code == 200
        resp = client.post(
            f"/api/v1/recycle-bin/notices/{note['id']}/restore",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "published"
        stats = client.get(
            f"/api/v1/notices/{note['id']}/statistics", headers=admin_headers
        ).json()
        assert stats["target_user_count"] == 1  # 只算 normal_user（管理员不进分母）
        titles = [
            n["title"]
            for n in client.get("/api/v1/notices", headers=user_headers).json()["items"]
        ]
        assert "被删通知" in titles

    def test_restore_published_notice_keeps_read_stats(
        self, client, admin_headers, user_headers, admin_user, normal_user
    ):
        notice = _create_notice(client, admin_headers, content="统计保留")
        _publish(client, admin_headers, notice["id"])
        client.post(f"/api/v1/notices/{notice['id']}/read", headers=user_headers, json={})
        _cancel(client, admin_headers, notice["id"])
        resp = client.post(
            f"/api/v1/recycle-bin/notices/{notice['id']}/restore",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "published"
        stats = client.get(
            f"/api/v1/notices/{notice['id']}/statistics", headers=admin_headers
        ).json()
        assert stats["target_user_count"] == 1  # 只算普通用户
        assert stats["viewed_count"] == 1

    def test_restore_expire_notice_reapplies_auto_expire(
        self, client, admin_headers, admin_user, normal_user
    ):
        doc = create_document(
            client, admin_headers, title="被废止资料D", effective_from="2020-01-01"
        )
        notice = _create_notice(
            client, admin_headers, notice_type="废止", content="废止D",
            document_ids=[doc["id"]],
        )
        _publish(client, admin_headers, notice["id"])
        today = date.today().isoformat()
        assert _doc_detail(client, admin_headers, doc["id"])["effective_to"] == today
        # 删除 → 联动回滚（资料恢复有效）
        _cancel(client, admin_headers, notice["id"])
        assert _doc_detail(client, admin_headers, doc["id"])["effective_to"] is None
        # 恢复 → 联动重新应用（与发布语义对称，避免「废止通知可见但资料有效」的矛盾）
        resp = client.post(
            f"/api/v1/recycle-bin/notices/{notice['id']}/restore",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert _doc_detail(client, admin_headers, doc["id"])["effective_to"] == today

    def test_cancel_after_restore_rolls_back_again(
        self, client, admin_headers, admin_user, normal_user
    ):
        doc = create_document(
            client, admin_headers, title="往返资料E", effective_from="2020-01-01"
        )
        notice = _create_notice(
            client, admin_headers, notice_type="废止", content="废止E",
            document_ids=[doc["id"]],
        )
        _publish(client, admin_headers, notice["id"])
        _cancel(client, admin_headers, notice["id"])
        client.post(
            f"/api/v1/recycle-bin/notices/{notice['id']}/restore", headers=admin_headers
        )
        # 再次删除：回滚保护仍生效（auto_expired_docs 已重新记录）
        _cancel(client, admin_headers, notice["id"])
        assert _doc_detail(client, admin_headers, doc["id"])["effective_to"] is None


class TestRecycleRestoreCache:
    """回收站恢复资料后，资料列表缓存立即失效。"""

    def test_restored_document_visible_in_list_immediately(
        self, client, admin_headers, user_headers, admin_user, normal_user
    ):
        doc = create_document(client, admin_headers, title="恢复缓存资料F")
        # 预热普通用户列表缓存（含该资料）
        items = client.get("/api/v1/documents", headers=user_headers).json()["items"]
        assert doc["id"] in [d["id"] for d in items]
        # 删除（列表缓存被刷新为"无该资料"）
        resp = client.delete(f"/api/v1/documents/{doc['id']}", headers=admin_headers)
        assert resp.status_code == 200
        items = client.get("/api/v1/documents", headers=user_headers).json()["items"]
        assert doc["id"] not in [d["id"] for d in items]
        # 从回收站恢复
        entries = client.get(
            "/api/v1/recycle-bin", headers=admin_headers
        ).json()["items"]
        entry = next(e for e in entries if e["document_id"] == doc["id"])
        resp = client.post(
            f"/api/v1/recycle-bin/{entry['id']}/restore", headers=admin_headers
        )
        assert resp.status_code == 200
        # 修复点：恢复后立即可见（不被 5 秒 TTL 旧缓存遮蔽）
        items = client.get("/api/v1/documents", headers=user_headers).json()["items"]
        assert doc["id"] in [d["id"] for d in items]


class TestVocabularyConsistency:
    """停用类型/已删除分类不能继续使用（词表一致性）。"""

    def test_delete_tag_removes_links_not_documents(self, client, admin_headers):
        """2026-11-09：删除标签只解除 document_tags，资料变未打标签，不删资料。"""
        tag_id = client.post("/api/v1/tags", headers=admin_headers,
                             json={"name": "待删除标签"}).json()["id"]
        doc = create_document(client, admin_headers, title="带待删标签资料",
                              effective_from="2026-01-01", tag_ids=[tag_id])
        resp = client.delete(f"/api/v1/tags/{tag_id}", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["affected_document_count"] == 1
        detail = client.get(f"/api/v1/documents/{doc['id']}", headers=admin_headers).json()
        assert detail["tags"] == [], "资料应保留，但不再带该标签"
        untagged = client.get("/api/v1/documents?untagged=1", headers=admin_headers).json()
        assert doc["id"] in [d["id"] for d in untagged["items"]]

    def test_delete_notice_type_unclassifies_notices(
        self, client, admin_headers, admin_user, normal_user
    ):
        """2026-11-09：普通类型可删除，已有通知类型变未分类；废止/替换仍保护。"""
        resp = client.post("/api/v1/notice-types", headers=admin_headers,
                           json={"name": "走查专用类型", "sort_order": 99})
        assert resp.status_code in (200, 201), resp.text
        new_id = resp.json()["id"]
        notice = _create_notice(client, admin_headers, title="引用类型", content="x",
                                notice_type="走查专用类型")
        resp = client.delete(f"/api/v1/notice-types/{new_id}", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["affected_notice_count"] == 1
        detail = client.get(f"/api/v1/notices/{notice['id']}", headers=admin_headers).json()
        assert detail["notice_type"] is None
        unclassified = client.get(
            "/api/v1/notices?type=__unclassified__", headers=admin_headers
        ).json()
        assert notice["id"] in [n["id"] for n in unclassified["items"]]
        # 同名可重建
        resp = client.post("/api/v1/notice-types", headers=admin_headers,
                           json={"name": "走查专用类型", "sort_order": 99})
        assert resp.status_code in (200, 201), resp.text
        # 废止/替换是资料时效联动类型，不能删除
        items = client.get("/api/v1/notice-types", headers=admin_headers).json()["items"]
        abolish = next(t for t in items if t["name"] == "废止")
        resp = client.delete(f"/api/v1/notice-types/{abolish['id']}", headers=admin_headers)
        assert resp.status_code == 409
        assert "联动" in resp.json()["error"]["message"]

    def test_notice_type_rename_cascades_to_notices(self, client, admin_headers):
        """改名必须级联：notices.notice_type 存裸字符串，不级联则老通知再也存不动、
        且引用计数按新名算=0 会让仍被引用的类型被误删。"""
        created = client.post("/api/v1/notice-types", headers=admin_headers,
                              json={"name": "培训通知", "sort_order": 98})
        assert created.status_code in (200, 201), created.text
        type_id = created.json()["id"]
        notice = _create_notice(client, admin_headers, title="培训安排", content="x",
                                notice_type="培训通知")

        renamed = client.put(f"/api/v1/notice-types/{type_id}", headers=admin_headers,
                             json={"name": "培训公告"})
        assert renamed.status_code == 200, renamed.text

        detail = client.get(f"/api/v1/notices/{notice['id']}", headers=admin_headers).json()
        assert detail["notice_type"] == "培训公告"

        again = client.put(f"/api/v1/notices/{notice['id']}", headers=admin_headers,
                           json={"title": "培训安排（改）", "content": "x",
                                 "notice_type": "培训公告"})
        assert again.status_code == 200, again.text  # 不再被判「类型不存在」

        deleted = client.delete(f"/api/v1/notice-types/{type_id}", headers=admin_headers)
        assert deleted.status_code == 200, deleted.text
        detail = client.get(f"/api/v1/notices/{notice['id']}", headers=admin_headers).json()
        assert detail["notice_type"] is None, "删除类型后通知应变为未分类"

    def test_auto_expire_type_rename_forbidden(self, client, admin_headers):
        """「废止/替换」按名称驱动资料时效联动，改名会静默断开 → 必须禁止。"""
        items = client.get("/api/v1/notice-types", headers=admin_headers).json()["items"]
        abolish = next(t for t in items if t["name"] == "废止")
        resp = client.put(f"/api/v1/notice-types/{abolish['id']}", headers=admin_headers,
                          json={"name": "作废"})
        assert resp.status_code == 409
        assert "联动" in resp.json()["error"]["message"]

    def test_deleted_category_rejected_for_documents(
        self, client, admin_headers, admin_user, normal_user
    ):
        resp = client.post(
            "/api/v1/categories", headers=admin_headers, json={"name": "临时分类"}
        )
        assert resp.status_code == 201
        cat_id = resp.json()["id"]
        resp = client.delete(f"/api/v1/categories/{cat_id}", headers=admin_headers)
        assert resp.status_code == 200
        # 新建资料引用已删除分类 → 400
        resp = client.post(
            "/api/v1/documents",
            headers=admin_headers,
            json={"title": "分类校验资料", "effective_from": "2026-01-01",
                  "category_id": cat_id},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        # 修改既有资料为已删除分类 → 同样阻止
        doc = create_document(client, admin_headers, title="改分类校验资料G")
        resp = client.put(
            f"/api/v1/documents/{doc['id']}",
            headers=admin_headers,
            json={"category_id": cat_id},
        )
        assert resp.status_code == 400


class TestAdminUnreadConsistency:
    """管理员未读：工作台计数与 unread_only 列表口径一致（2026-09-06 口径反转）。

    2026-08-30 曾对齐为 LEFT JOIN（未定向通知也计入管理员未读）；实际使用
    中非对象通知打开详情不置读，管理员工作台会挂一组永远清不掉的未读数字。
    2026-09-06 产品决策：不需要我查看的就不该出现在工作台——所有角色统一
    为「仅统计自己是阅读对象（快照行存在）且未读」。
    """

    def test_admin_unread_excludes_untargeted_and_matches_list(
        self, client, admin_headers, user_factory, admin_user, normal_user
    ):
        u1 = user_factory("定向员工", role="user")
        notice = _create_notice(
            client, admin_headers, content="只发给员工，未定向给管理员",
            target={"scope": "user", "user_ids": [u1.id]},
        )
        _publish(client, admin_headers, notice["id"])

        # 工作台（admin）：未定向通知不计入未读（admin 非该通知阅读对象）
        dash = client.get("/api/v1/dashboard", headers=admin_headers).json()
        assert dash["my_unread_notices"] == 0

        # 员工（对象内）：计入未读
        u1_headers = login_headers(client, "定向员工", "user1234")
        dash_u1 = client.get("/api/v1/dashboard", headers=u1_headers).json()
        assert dash_u1["my_unread_notices"] == 1

        # 列表 unread_only（admin）：与工作台计数严格一致，且未定向通知不在其中
        unread_list = client.get(
            "/api/v1/notices", headers=admin_headers, params={"unread_only": True}
        ).json()
        assert unread_list["total"] == dash["my_unread_notices"] == 0
        assert not any(n["id"] == notice["id"] for n in unread_list["items"])


class TestCountFilterConsistency:
    """展示出来的数字必须等于点进去看到的条数（§21/§25 + 「与 API 真值一致」纪律）。"""

    def test_expiring_filter_window(self, client, admin_headers):
        """status=expiring 口径：仅「已生效且 30 天内失效」的资料。

        2026-11-03：管理待办卡已移除，原「卡片数字 = 列表条数」的一致性断言
        不再适用；此处保留对筛选窗口本身的语义校验（30 天窗口 + 排除历史过期）。
        """
        today = date.today()

        def mk(title, days_from, days_to):
            return create_document(
                client, admin_headers, title=title,
                effective_from=(today + timedelta(days=days_from)).isoformat(),
                effective_to=(today + timedelta(days=days_to)).isoformat()
                if days_to is not None else None,
            )
        mk("十天后到期", -5, 10)
        mk("半年前就过期", -400, -100)
        mk("六十天后到期", -5, 60)
        mk("暂无失效日期", -5, None)

        listed = client.get("/api/v1/documents", headers=admin_headers,
                            params={"status": "expiring"}).json()["total"]
        assert listed == 1
        # 修复前卡片链接用 status=expired + as_of=今天+30，会把历史过期一并捞进来
        stale = client.get("/api/v1/documents", headers=admin_headers, params={
            "status": "expired",
            "as_of_date": (today + timedelta(days=30)).isoformat(),
        }).json()["total"]
        assert stale == 2

    def test_tag_count_matches_tag_filter(self, client, admin_headers):
        """标签 document_count 与按标签筛选的条数同口径（软删资料不计）。"""
        tag_id = client.post("/api/v1/tags", headers=admin_headers,
                             json={"name": "口径标签"}).json()["id"]
        create_document(client, admin_headers, title="在架资料", effective_from="2026-01-01",
                        tag_ids=[tag_id])
        gone = create_document(client, admin_headers, title="将归档", effective_from="2026-01-01",
                               tag_ids=[tag_id])["id"]
        client.request("DELETE", f"/api/v1/documents/{gone}", headers=admin_headers,
                       json={"reason": "测试归档"})

        tags = client.get("/api/v1/tags", headers=admin_headers,
                          params={"with_count": 1}).json()["items"]
        count = next(t for t in tags if t["id"] == tag_id)["document_count"]
        listed = client.get("/api/v1/documents", headers=admin_headers,
                            params={"tag_id": tag_id}).json()["total"]
        assert count == listed == 1

    def test_audience_roster_matches_statistics(self, client, admin_headers, user_factory):
        """点名册、统计分母、列表率条分母三处同口径：只算启用的应阅读用户。"""
        u1 = user_factory("在读甲", role="user")
        u2 = user_factory("停用乙", role="user")
        client.put(f"/api/v1/users/{u2.id}", headers=admin_headers,
                   json={"is_active": False})
        notice = _create_notice(client, admin_headers, content="发给两人",
                                target={"scope": "user", "user_ids": [u1.id, u2.id]})
        _publish(client, admin_headers, notice["id"])

        stats = client.get(f"/api/v1/notices/{notice['id']}/statistics",
                           headers=admin_headers).json()
        roster = client.get(f"/api/v1/notices/{notice['id']}/audience",
                            headers=admin_headers).json()
        assert stats["target_user_count"] == roster["total"] == 1

        rows = client.get("/api/v1/notices", headers=admin_headers).json()["items"]
        row = next(n for n in rows if n["id"] == notice["id"])
        assert row["read_stats"]["target"] == 1

    def test_audit_date_to_includes_whole_end_day(self, client, admin_headers):
        """结束日期为纯日期时按含当日全天处理（此前漏掉结束日一整天）。"""
        client.post("/api/v1/tags", headers=admin_headers, json={"name": "留痕标签"})
        today = date.today().isoformat()
        same_day = client.get("/api/v1/audit-logs", headers=admin_headers,
                              params={"date_to": today}).json()["total"]
        assert same_day >= 1
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        assert client.get("/api/v1/audit-logs", headers=admin_headers,
                          params={"date_to": yesterday}).json()["total"] == 0
