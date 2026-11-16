"""回归：通知「关联资料」必须遵守资料可见范围（2026-11-16 验收实测缺陷）。

缺陷
----
`GET /api/v1/notices/{id}/documents` 与非 admin 的 `GET /api/v1/notices/{id}`
里的 `documents[]`，对普通用户只过滤了「已归档」，**没有**过滤资料的
`audience_scope` / `document_targets`。于是：

    通知发给全体 → 关联了「仅 HR 可见」的资料
    → 非 HR 普通用户能拿到该资料的 id / title / status

而直接访问 `GET /api/v1/documents/{id}` 是 404。也就是说**标题级元数据越权泄露**，
即 项目规范 §33「普通用户不能读取无权限文件」的同类问题。

既有测试只覆盖「已归档资料不展示」（tests/test_logic_consistency.py），
从未覆盖「可见范围不匹配」，所以缺陷一直没被发现。

修复口径
--------
与详情/下载/预览完全一致：复用 `document_visibility.is_document_visible`。
不改变接口契约，只补上缺失的过滤条件。
"""
from __future__ import annotations

from helpers import create_document


def _make_notice(client, headers, document_ids, title="可见性回归通知"):
    return client.post(
        "/api/v1/notices",
        headers=headers,
        json={
            "title": title,
            "content": "正文",
            "document_ids": document_ids,
            "target": {"scope": "all", "department_ids": [], "user_ids": []},
        },
    )


def _scoped_document(client, admin_headers, visible_user_id, title):
    """一份只对指定用户可见的资料。"""
    resp = client.post(
        "/api/v1/documents",
        headers=admin_headers,
        json={
            "title": title,
            "summary": "仅指定人员可见",
            "effective_from": "2026-01-01",
            "audience_scope": "user",
            "audience_user_ids": [visible_user_id],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestLinkedDocumentsRespectAudience:
    def test_linked_endpoint_hides_out_of_scope_document(
        self, client, admin_headers, user_headers, normal_user, user_factory
    ):
        """非阅读对象之外的资料不得出现在 /notices/{id}/documents。"""
        other = user_factory("other_person", display_name="另一个人")
        secret = _scoped_document(client, admin_headers, other.id, "仅他人可见的资料")

        notice = _make_notice(client, admin_headers, [secret["id"]])
        assert notice.status_code == 201, notice.text
        notice_id = notice.json()["id"]

        linked = client.get(
            f"/api/v1/notices/{notice_id}/documents", headers=user_headers
        )
        assert linked.status_code == 200
        ids = [item["id"] for item in linked.json()["items"]]
        assert secret["id"] not in ids, (
            "越权泄露：普通用户通过通知关联资料看到了自己无权查看的资料 "
            f"{secret['title']!r}（id={secret['id']}）"
        )

    def test_notice_detail_documents_hides_out_of_scope_document(
        self, client, admin_headers, user_headers, normal_user, user_factory
    ):
        """详情序列化里的 documents[] 同样不得泄露。"""
        other = user_factory("other_person2", display_name="另一个人2")
        secret = _scoped_document(client, admin_headers, other.id, "仅他人可见的资料2")

        notice = _make_notice(client, admin_headers, [secret["id"]], title="详情泄露回归")
        notice_id = notice.json()["id"]

        detail = client.get(f"/api/v1/notices/{notice_id}", headers=user_headers)
        assert detail.status_code == 200
        ids = [d["id"] for d in detail.json()["documents"]]
        assert secret["id"] not in ids, "越权泄露：通知详情 documents[] 暴露了无权资料"

    def test_in_scope_document_is_still_listed(
        self, client, admin_headers, user_headers, normal_user
    ):
        """范围命中时必须照常展示——修复不能把正常关联一起过滤掉。"""
        mine = _scoped_document(client, admin_headers, normal_user.id, "仅本人可见的资料")

        notice = _make_notice(client, admin_headers, [mine["id"]], title="范围内回归")
        notice_id = notice.json()["id"]

        linked = client.get(
            f"/api/v1/notices/{notice_id}/documents", headers=user_headers
        )
        ids = [item["id"] for item in linked.json()["items"]]
        assert mine["id"] in ids, "本人可见的关联资料被错误隐藏"

        detail = client.get(f"/api/v1/notices/{notice_id}", headers=user_headers)
        assert mine["id"] in [d["id"] for d in detail.json()["documents"]]

    def test_all_scope_document_still_visible(
        self, client, admin_headers, user_headers
    ):
        """scope=all 的资料对任何登录用户都应可见。"""
        public = create_document(client, admin_headers, title="全体可见资料")
        notice = _make_notice(client, admin_headers, [public["id"]], title="全体范围回归")
        notice_id = notice.json()["id"]

        linked = client.get(
            f"/api/v1/notices/{notice_id}/documents", headers=user_headers
        )
        assert public["id"] in [item["id"] for item in linked.json()["items"]]

    def test_admin_still_sees_everything(
        self, client, admin_headers, admin_user, user_factory
    ):
        """管理员保持全量视角（管理职责），不受可见范围限制。"""
        other = user_factory("other_person3", display_name="另一个人3")
        secret = _scoped_document(client, admin_headers, other.id, "仅他人可见的资料3")

        notice = _make_notice(client, admin_headers, [secret["id"]], title="管理员全量回归")
        notice_id = notice.json()["id"]

        linked = client.get(
            f"/api/v1/notices/{notice_id}/documents", headers=admin_headers
        )
        assert secret["id"] in [item["id"] for item in linked.json()["items"]]
