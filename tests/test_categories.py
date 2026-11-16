"""分类深度与子树口径回归（项目规范 §9/§30、docs/API.md §4/§6、docs/DATABASE.md §4）。

覆盖 2026-09「分类深度放宽至四级」中未落地的部分：
- 一~四级建链、第五级拒绝、重挂成环拦截
- 按分类筛选含**全部子孙**（此前只含直接子分类一层，三级资料从一级入口查不到）
- 分类树 document_count 与筛选结果同口径
- 指定 parent_id 的平级列表语义
"""
from __future__ import annotations

import pytest

from helpers import create_document

pytestmark = pytest.mark.usefixtures("_data_isolation_guard")


def _cat(client, headers, name, parent_id=None):
    payload = {"name": name}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    resp = client.post("/api/v1/categories", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _tree(client, headers):
    """技术 → 后端 → Go → Go并发规范（一~四级）。"""
    l1 = _cat(client, headers, "技术")
    l2 = _cat(client, headers, "后端", l1["id"])
    l3 = _cat(client, headers, "Go", l2["id"])
    l4 = _cat(client, headers, "Go并发规范", l3["id"])
    return l1, l2, l3, l4


def _node(tree_data, cat_id):
    stack = list(tree_data["items"])
    while stack:
        n = stack.pop()
        if n["id"] == cat_id:
            return n
        stack.extend(n.get("children") or [])
    raise AssertionError(f"树中未找到分类 {cat_id}")


class TestDepthLimit:
    def test_four_levels_chain_ok(self, client, admin_headers):
        l1, l2, l3, l4 = _tree(client, admin_headers)
        data = client.get("/api/v1/categories", headers=admin_headers).json()
        n1 = _node(data, l1["id"])
        n2 = _node(data, l2["id"])
        n3 = _node(data, l3["id"])
        assert n2["id"] == n1["children"][0]["id"]
        assert n3["id"] == n2["children"][0]["id"]
        assert n3["children"][0]["id"] == l4["id"]

    def test_fifth_level_rejected(self, client, admin_headers):
        _, _, _, l4 = _tree(client, admin_headers)
        resp = client.post(
            "/api/v1/categories",
            headers=admin_headers,
            json={"name": "第五级", "parent_id": l4["id"]},
        )
        assert resp.status_code == 400
        assert "4 级" in resp.json()["error"]["message"]

    def test_document_on_level3_reports_full_path(self, client, admin_headers):
        _, _, l3, _ = _tree(client, admin_headers)
        doc = create_document(client, admin_headers, title="三级资料",
                              effective_from="2026-01-01", category_id=l3["id"])
        assert doc["category_path"] == ["技术", "后端", "Go"]

    def test_move_under_own_descendant_rejected(self, client, admin_headers):
        l1, _, l3, _ = _tree(client, admin_headers)
        resp = client.put(
            f"/api/v1/categories/{l1['id']}",
            headers=admin_headers,
            json={"parent_id": l3["id"]},
        )
        assert resp.status_code == 400
        assert "子孙" in resp.json()["error"]["message"]

    def test_move_subtree_exceeding_depth_rejected(self, client, admin_headers):
        """移动带子树的分类时，必须校验**整棵子树**移动后的深度（§9 上限 4 级）。

        2026-10-29 回归守卫。此前 update_category 只校验被移动节点自身的
        祖先链深度，不管它的子孙——于是把「带 3 级子树」的节点挂到 3 级分类下，
        返回 200，子树最深节点变成 7 级，静默突破上限。
        """
        # 技术(1) → 后端(2) → Go(3) → Go并发规范(4)
        l1, l2, l3, l4 = _tree(client, admin_headers)
        # 另建一棵 A(1) → A1(2)，整体挂到 l3(3级) 下会让 A1 变 5 级
        a = _cat(client, admin_headers, "A")
        a1 = _cat(client, admin_headers, "A1", a["id"])

        resp = client.put(
            f"/api/v1/categories/{a['id']}",
            headers=admin_headers,
            json={"parent_id": l3["id"]},
        )
        assert resp.status_code == 400, f"应拦截但返回 {resp.status_code}: {resp.text}"
        assert "4 级" in resp.json()["error"]["message"]

        # 树未被改动（事务回滚）：A 仍在根级，A1 仍挂在 A 下
        data = client.get("/api/v1/categories", headers=admin_headers).json()
        a_node = _node(data, a["id"])
        assert a_node["parent_id"] is None
        assert a_node["children"][0]["id"] == a1["id"]

    def test_move_single_node_to_exact_depth_ok(self, client, admin_headers):
        """边界：无子树的节点挂到 3 级下 = 恰好第 4 级，必须允许（不能过度拦截）。"""
        _, _, l3, _ = _tree(client, admin_headers)
        leaf = _cat(client, admin_headers, "叶子")
        resp = client.put(
            f"/api/v1/categories/{leaf['id']}",
            headers=admin_headers,
            json={"parent_id": l3["id"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["parent_id"] == l3["id"]


class TestSubtreeFiltering:
    def test_root_filter_hits_level3_and_level4(self, client, admin_headers):
        """选中一级分类必须能看到三/四级下的资料（文件夹语义）。"""
        l1, l2, l3, l4 = _tree(client, admin_headers)
        _cat(client, admin_headers, "财务")  # 干扰项：另一棵无资料树
        create_document(client, admin_headers, title="挂二级",
                        effective_from="2026-01-01", category_id=l2["id"])
        create_document(client, admin_headers, title="挂三级",
                        effective_from="2026-01-01", category_id=l3["id"])
        create_document(client, admin_headers, title="挂四级",
                        effective_from="2026-01-01", category_id=l4["id"])

        def titles_of(cat_id):
            resp = client.get(
                f"/api/v1/documents?category_id={cat_id}&page_size=50",
                headers=admin_headers,
            )
            return {d["title"] for d in resp.json()["items"]}

        assert titles_of(l1["id"]) == {"挂二级", "挂三级", "挂四级"}
        assert titles_of(l2["id"]) == {"挂二级", "挂三级", "挂四级"}
        assert titles_of(l3["id"]) == {"挂三级", "挂四级"}
        assert titles_of(l4["id"]) == {"挂四级"}

    def test_sibling_subtree_not_leaked(self, client, admin_headers):
        """三级分类筛不到兄弟子树的资料。"""
        l1, l2, l3, _ = _tree(client, admin_headers)
        other = _cat(client, admin_headers, "前端", l1["id"])
        create_document(client, admin_headers, title="前端资料",
                        effective_from="2026-01-01", category_id=other["id"])
        keep = create_document(client, admin_headers, title="Go资料",
                               effective_from="2026-01-01", category_id=l3["id"])
        items = client.get(
            f"/api/v1/documents?category_id={l2['id']}", headers=admin_headers
        ).json()["items"]
        assert [d["id"] for d in items] == [keep["id"]]

    def test_archived_document_excluded(self, client, admin_headers):
        l1, _, l3, _ = _tree(client, admin_headers)
        doc = create_document(client, admin_headers, title="将被归档",
                              effective_from="2026-01-01", category_id=l3["id"])
        client.request("DELETE", f"/api/v1/documents/{doc['id']}",
                       headers=admin_headers, json={"reason": "不再使用"})
        total = client.get(
            f"/api/v1/documents?category_id={l1['id']}", headers=admin_headers
        ).json()["total"]
        assert total == 0


class TestCountConsistency:
    def test_tree_count_equals_filter_result(self, client, admin_headers):
        """树上各级计数 = 点进该分类能看到的资料数（口径一致）。"""
        l1, l2, l3, l4 = _tree(client, admin_headers)
        create_document(client, admin_headers, title="一级直属",
                        effective_from="2026-01-01", category_id=l1["id"])
        create_document(client, admin_headers, title="三级资料",
                        effective_from="2026-01-01", category_id=l3["id"])
        create_document(client, admin_headers, title="四级资料",
                        effective_from="2026-01-01", category_id=l4["id"])

        data = client.get(
            "/api/v1/categories?with_document_count=1", headers=admin_headers
        ).json()
        expect = {l1["id"]: 3, l2["id"]: 2, l3["id"]: 2, l4["id"]: 1}
        for cat_id, want in expect.items():
            node = _node(data, cat_id)
            assert node["document_count"] == want, f"分类 {cat_id} 计数不符"
            listed = client.get(
                f"/api/v1/documents?category_id={cat_id}", headers=admin_headers
            ).json()["total"]
            assert listed == node["document_count"], "计数与筛选结果不同口径"

    def test_flat_parent_list_uses_subtree_count(self, client, admin_headers):
        """指定 parent_id 的平级列表：无 children 字段，但计数仍为子树口径。"""
        l1, l2, l3, _ = _tree(client, admin_headers)
        create_document(client, admin_headers, title="三级资料",
                        effective_from="2026-01-01", category_id=l3["id"])
        data = client.get(
            f"/api/v1/categories?parent_id={l1['id']}&with_document_count=1",
            headers=admin_headers,
        ).json()
        assert len(data["items"]) == 1
        node = data["items"][0]
        assert "children" not in node
        assert node["document_count"] == 1


class TestDeleteTreeSemantics:
    def test_delete_deep_ancestor_removes_subtree_and_unclassifies_document(self, client, admin_headers):
        """2026-11-09：删一级祖先 = 删整棵四层树；三级资料变未分类，资料本身保留。"""
        l1, l2, l3, l4 = _tree(client, admin_headers)
        doc = create_document(client, admin_headers, title="三级资料",
                              effective_from="2026-01-01", category_id=l3["id"])
        resp = client.request("DELETE", f"/api/v1/categories/{l1['id']}",
                              headers=admin_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["deleted_category_count"] == 4
        assert body["unclassified_document_count"] == 1
        assert set(body["deleted_category_ids"]) == {l1["id"], l2["id"], l3["id"], l4["id"]}

        data = client.get("/api/v1/categories", headers=admin_headers).json()["items"]
        remaining_ids = set()
        stack = list(data)
        while stack:
            node = stack.pop()
            remaining_ids.add(node["id"])
            stack.extend(node.get("children") or [])
        assert not ({l1["id"], l2["id"], l3["id"], l4["id"]} & remaining_ids)
        detail = client.get(f"/api/v1/documents/{doc['id']}", headers=admin_headers).json()
        assert detail["category_id"] is None, "资料应变为未分类，而不是被删除"
