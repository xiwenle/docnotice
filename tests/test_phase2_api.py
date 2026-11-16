"""阶段2 后端补充回归：dashboard 聚合 / 分类含子级 / tags 名 / 未读筛选。

对应 UI 改版规划（工作台待办卡、资料页文件夹语义、通知页未读 tab）的数据能力。
"""
from __future__ import annotations


import pytest

from helpers import create_document, create_notice

pytestmark = pytest.mark.usefixtures("_data_isolation_guard")


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




def _create_category(client, headers, name, parent_id=None):
    payload = {"name": name}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    resp = client.post("/api/v1/categories", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestDashboardAggregates:
    def test_my_unread_notices_flow(self, client, admin_headers, user_headers):
        """发布通知 → 我的未读=1；标记已查看 → 0（缓存随写失效）。"""
        notice = create_notice(
            client, admin_headers, title="新公告", content="请阅读",
            target={"scope": "all"},
        )
        _publish(client, admin_headers, notice["id"])

        data = client.get("/api/v1/dashboard", headers=user_headers).json()
        assert data["my_unread_notices"] == 1

        resp = client.post(
            f"/api/v1/notices/{notice['id']}/read", headers=user_headers,
        )
        assert resp.status_code == 200, resp.text
        data = client.get("/api/v1/dashboard", headers=user_headers).json()
        assert data["my_unread_notices"] == 0

    def test_admin_todo_counts_removed(self, client, admin_headers):
        """2026-11-03 产品决策：管理员「管理待办」五卡已移除，聚合字段不再返回。

        保留此用例防止字段被悄悄加回来——管理员明确要求"不需要系统告诉他
        该做什么"。仅 last_backup_at（sparse 健康行用）保留。
        """
        create_document(client, admin_headers, title="未分类资料",
                        effective_from="2026-01-01")
        create_notice(client, admin_headers, title="草稿通知", content="草稿正文")

        data = client.get("/api/v1/dashboard", headers=admin_headers).json()
        for field in ("uncategorized_count", "untagged_count",
                      "expiring_30d_count", "draft_notice_count"):
            assert field not in data, f"{field} 应随管理待办卡一并移除"
        assert "last_backup_at" in data

    def test_normal_user_dashboard_has_no_admin_fields(self, client, user_headers):
        data = client.get("/api/v1/dashboard", headers=user_headers).json()
        for field in ("uncategorized_count", "untagged_count",
                      "expiring_30d_count", "draft_notice_count", "last_backup_at"):
            assert field not in data


class TestCategoryDescendantSemantics:
    def test_filter_by_parent_includes_children(self, client, admin_headers):
        """按一级分类筛选应含其二级分类下的资料（文件夹语义）。"""
        root = _create_category(client, admin_headers, "制度规范")
        child = _create_category(client, admin_headers, "管理制度", parent_id=root["id"])
        create_document(client, admin_headers, title="挂二级的资料",
                        effective_from="2026-01-01", category_id=child["id"])

        resp = client.get(
            f"/api/v1/documents?category_id={root['id']}", headers=admin_headers
        )
        assert resp.status_code == 200
        titles = [d["title"] for d in resp.json()["items"]]
        assert "挂二级的资料" in titles

    def test_tree_count_includes_children(self, client, admin_headers):
        """分类树一级计数 = 自身 + 子级资料数。"""
        root = _create_category(client, admin_headers, "技术标准")
        child = _create_category(client, admin_headers, "设计规范", parent_id=root["id"])
        create_document(client, admin_headers, title="子级资料",
                        effective_from="2026-01-01", category_id=child["id"])
        create_document(client, admin_headers, title="直属资料",
                        effective_from="2026-01-01", category_id=root["id"])

        data = client.get(
            "/api/v1/categories?with_document_count=1", headers=admin_headers
        ).json()
        node = next(c for c in data["items"] if c["id"] == root["id"])
        assert node["document_count"] == 2


class TestTagsSerialization:
    def test_list_items_include_tag_names(self, client, admin_headers):
        resp = client.post("/api/v1/tags", headers=admin_headers, json={"name": "财务"})
        assert resp.status_code in (200, 201), resp.text
        tag_id = resp.json()["id"]
        create_document(client, admin_headers, title="带标签资料",
                        effective_from="2026-01-01", tag_ids=[tag_id])

        items = client.get("/api/v1/documents", headers=admin_headers).json()["items"]
        doc = next(d for d in items if d["title"] == "带标签资料")
        assert doc["tags"] == [{"id": tag_id, "name": "财务"}]


class TestUnreadFilter:
    """未读筛选（原 needs_confirm 筛选已随确认功能下线移除）。"""

    def test_filter_returns_only_unread(self, client, admin_headers, user_headers):
        n1 = create_notice(client, admin_headers, title="未读的", content="正文",
                           target={"scope": "all"})
        _publish(client, admin_headers, n1["id"])
        resp = client.get("/api/v1/notices?unread_only=true", headers=user_headers)
        assert resp.status_code == 200
        assert "未读的" in [n["title"] for n in resp.json()["items"]]

    def test_filter_excludes_after_read(self, client, admin_headers, user_headers):
        n1 = create_notice(client, admin_headers, title="待读甲", content="正文",
                           target={"scope": "all"})
        _publish(client, admin_headers, n1["id"])
        assert client.get(
            "/api/v1/notices?unread_only=true", headers=user_headers
        ).json()["total"] == 1

        client.post(f"/api/v1/notices/{n1['id']}/read", headers=user_headers)
        assert client.get(
            "/api/v1/notices?unread_only=true", headers=user_headers
        ).json()["total"] == 0
