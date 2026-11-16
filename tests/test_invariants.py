"""五项状态一致性审计修复（commit 8aa24a5）的不变量回归测试。

授权范围：任务三。与既有测试互补不重复：
- tests/test_documents.py::TestArchive 已覆盖「普通用户 status=archived 不可见 +
  详情/下载/预览 403」；本文件覆盖全部 status 枚举矩阵与 admin 两个口径，不重复断言 403。
- tests/test_logic_consistency.py::TestVocabularyConsistency 已覆盖「已删除分类」
  创建/更新两条路径；本文件覆盖「停用（is_active=False）分类」的创建/更新/批量整理
  三条路径（上轮修复点：_load_category 统一校验）。

不变量清单：
1. 回收站列表只显示「在站」记录：restored_at / purged_at 非空的条目一律不出现
   （recycle_service.list_recycle）
2. 阅读统计分母只含启用用户（§21）：发布后停用 → target_user_count 同步减少；
   重新启用 → 恢复（notice_service.get_statistics JOIN users 过滤）
3. 归档资料可见性（§33）：普通用户任何 status 筛选都不可见；admin 默认列表不含
   archived，仅 status=archived 可见（document_service.list_documents）
4. 停用分类拦截：创建 / 更新 / 批量整理资料引用停用分类 → 400 VALIDATION_ERROR，
   且批量整理不产生部分更新（document_service._load_category）

注意：任务描述中的「422」与实现不符——本仓库错误码 VALIDATION_ERROR 统一映射
HTTP 400（app/core/errors.py ERROR_HTTP_STATUS），以实现为准。
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest
from files.generators import make_pdf
from sqlalchemy import func, select

from helpers import create_document, create_notice, upload_document_file


def _upload_ok(client, headers, doc_id, filename) -> None:
    """生成真实 PDF 并上传成功（走魔数+深度校验链路）。

    临时文件放在平台默认临时目录（`tempfile.gettempdir()`）：写死 `/tmp/`
    在 Windows 上不存在，会让整条用例在部署机失败（2026-11-02 实测）。
    """
    pdf = make_pdf(
        os.path.join(tempfile.gettempdir(), f"inv_{filename}"),
        title=filename,
        body=f"invariant test {filename}",
    )
    resp = upload_document_file(
        client, headers, doc_id, filename=filename,
        content=pdf.read_bytes(), mime="application/pdf",
    )
    assert resp.status_code == 200, f"上传失败 {resp.status_code}: {resp.text}"


def _entries(client, headers, **params) -> list[dict]:
    resp = client.get("/api/v1/recycle-bin", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


# ---------------------------------------------------------------------------
# 1) 回收站：restored / purged 记录不再显示
# ---------------------------------------------------------------------------
class TestRecycleBinHidesTerminalEntries:
    """回收站列表不变量：只有「在站」记录可见（修复点：restored 过滤）。"""

    def test_restored_and_purged_entries_hidden(
        self, client, admin_headers, tmp_path
    ):
        # 三条在站记录：replaced（替换产生）+ archived ×2（归档产生）
        doc_a = create_document(client, admin_headers, title="回收站不变量A",
                                effective_from="2026-01-01")["id"]
        _upload_ok(client, admin_headers, doc_a, "inv_a_v1.pdf")
        _upload_ok(client, admin_headers, doc_a, "inv_a_v2.pdf")  # 替换 → replaced 条目

        doc_b = create_document(client, admin_headers, title="回收站不变量B",
                                effective_from="2026-01-01")["id"]
        _upload_ok(client, admin_headers, doc_b, "inv_b.pdf")
        resp = client.request("DELETE", f"/api/v1/documents/{doc_b}",
                              headers=admin_headers, json={"reason": "测试归档B"})
        assert resp.status_code == 200

        doc_c = create_document(client, admin_headers, title="回收站不变量C",
                                effective_from="2026-01-01")["id"]
        _upload_ok(client, admin_headers, doc_c, "inv_c.pdf")
        resp = client.request("DELETE", f"/api/v1/documents/{doc_c}",
                              headers=admin_headers, json={"reason": "测试归档C"})
        assert resp.status_code == 200

        entries = {e["original_filename"]: e for e in _entries(client, admin_headers)}
        assert "inv_a_v1.pdf" in entries, "前置失败：替换条目应在回收站"
        assert "inv_b.pdf" in entries and "inv_c.pdf" in entries, "前置失败：归档条目应在回收站"

        # 恢复 replaced 条目 → restored_at 置值 → 不再显示
        resp = client.post(f"/api/v1/recycle-bin/{entries['inv_a_v1.pdf']['id']}/restore",
                           headers=admin_headers)
        assert resp.status_code == 200, resp.text
        # 永久删除 archived 条目 → purged_at 置值 → 不再显示
        resp = client.request("DELETE", f"/api/v1/recycle-bin/{entries['inv_b.pdf']['id']}",
                              headers=admin_headers, json={"confirm": True})
        assert resp.status_code == 200, resp.text

        names = [e["original_filename"] for e in _entries(client, admin_headers)]
        assert "inv_a_v1.pdf" not in names, "已恢复（restored_at 非空）的条目不应出现在回收站列表"
        assert "inv_b.pdf" not in names, "已永久删除（purged_at 非空）的条目不应出现在回收站列表"
        assert "inv_c.pdf" in names, "未处理的在站条目必须仍然可见"

        # reason 筛选口径同样只含在站记录
        archived_names = [e["original_filename"] for e in _entries(client, admin_headers, reason="archived")]
        assert "inv_b.pdf" not in archived_names
        replaced_names = [e["original_filename"] for e in _entries(client, admin_headers, reason="replaced")]
        assert "inv_a_v1.pdf" not in replaced_names


# ---------------------------------------------------------------------------
# 2) 阅读统计：分母排除停用用户
# ---------------------------------------------------------------------------
class TestStatisticsExcludesInactiveUsers:
    """target_user_count 不含停用用户（修复点：get_statistics JOIN users）。"""

    def _make_user(self, client, admin_headers, username: str) -> int:
        resp = client.post("/api/v1/users", headers=admin_headers, json={
            "username": username, "password": "Inv@Pass123",
            "display_name": username, "role": "user",
        })
        assert resp.status_code == 201, f"建户失败 {resp.status_code}: {resp.text}"
        return resp.json()["id"]

    def test_deactivate_then_reactivate_updates_denominator(
        self, client, admin_headers, admin_user
    ):
        u1 = self._make_user(client, admin_headers, "inv_stat_u1")
        u2 = self._make_user(client, admin_headers, "inv_stat_u2")

        resp = client.post("/api/v1/notices", headers=admin_headers, json={
            "title": "统计分母不变量通知", "content": "正文",
            "notice_type": "发布",
            "target": {"scope": "user", "user_ids": [u1, u2]},
        })
        assert resp.status_code in (200, 201), resp.text
        nid = resp.json()["id"]  # 2026-11-04：创建即发布
        assert resp.json()["status"] == "published"

        stats = client.get(f"/api/v1/notices/{nid}/statistics", headers=admin_headers).json()
        assert stats["target_user_count"] == 2, "发布时快照 2 人，分母应为 2"

        # 发布后停用 u2 → 分母同步 -1
        resp = client.delete(f"/api/v1/users/{u2}", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        stats = client.get(f"/api/v1/notices/{nid}/statistics", headers=admin_headers).json()
        assert stats["target_user_count"] == 1, "停用用户必须从统计分母排除"
        assert stats["unviewed_count"] == 1, "未查看数 = 分母 - 已查看，须同步收敛"

        # 重新启用 → 分母恢复（快照行仍在，仅 is_active 过滤）
        resp = client.put(f"/api/v1/users/{u2}", headers=admin_headers,
                          json={"is_active": True})
        assert resp.status_code == 200, resp.text
        stats = client.get(f"/api/v1/notices/{nid}/statistics", headers=admin_headers).json()
        assert stats["target_user_count"] == 2, "重新启用后分母应恢复"


# ---------------------------------------------------------------------------
# 3) 归档可见性：status 筛选矩阵
# ---------------------------------------------------------------------------
class TestArchivedVisibilityMatrix:
    """普通用户任何 status 筛选不可见归档；admin 仅 status=archived 可见。"""

    ALL_STATUSES = ("pending", "active", "expired", "archived")

    @pytest.fixture
    def archived_doc(self, client, admin_headers):
        doc_id = create_document(client, admin_headers, title="归档矩阵专用资料",
                                 effective_from="2026-01-01")["id"]
        _upload_ok(client, admin_headers, doc_id, "inv_arch.pdf")
        resp = client.request("DELETE", f"/api/v1/documents/{doc_id}",
                              headers=admin_headers, json={"reason": "矩阵测试归档"})
        assert resp.status_code == 200
        return doc_id

    def test_normal_user_never_sees_archived_under_any_status(
        self, client, user_headers, archived_doc
    ):
        # 空筛选（默认全部）+ 全部 status 枚举：一律不可见
        resp = client.get("/api/v1/documents", headers=user_headers)
        assert resp.status_code == 200
        assert all(d["id"] != archived_doc for d in resp.json()["items"]), \
            "默认列表不得包含归档资料"
        for status in self.ALL_STATUSES:
            resp = client.get(f"/api/v1/documents?status={status}", headers=user_headers)
            assert resp.status_code == 200
            assert all(d["id"] != archived_doc for d in resp.json()["items"]), \
                f"普通用户 status={status} 筛选不得包含归档资料"

    def test_admin_sees_archived_only_with_explicit_filter(
        self, client, admin_headers, archived_doc
    ):
        # admin 默认列表不含 archived
        resp = client.get("/api/v1/documents", headers=admin_headers)
        assert resp.status_code == 200
        assert all(d["id"] != archived_doc for d in resp.json()["items"]), \
            "admin 默认列表不显示归档资料"
        # 非 archived 的显式筛选也不含
        for status in ("pending", "active", "expired"):
            resp = client.get(f"/api/v1/documents?status={status}", headers=admin_headers)
            assert all(d["id"] != archived_doc for d in resp.json()["items"])
        # 仅 status=archived 可见，且状态标记正确
        resp = client.get("/api/v1/documents?status=archived", headers=admin_headers)
        assert resp.status_code == 200
        matched = [d for d in resp.json()["items"] if d["id"] == archived_doc]
        assert len(matched) == 1, "admin 必须能通过 status=archived 查到归档资料"
        assert matched[0]["archived"] is True
        assert matched[0]["status"] == "archived"


# ---------------------------------------------------------------------------
# 4) 停用分类拦截：创建 / 更新 / 批量整理
# ---------------------------------------------------------------------------
class TestLegacyDeletedCategoryRejected:
    """分类删除已改为直接物理删（2026-09-03），API 不再产生停用分类；
    历史遗留的 is_active=0 行（迁移保留的有引用行）仍必须被 _load_category
    拒绝被资料引用（守卫回归）。"""

    @pytest.fixture
    def legacy_deleted_category(self, client, admin_headers, category, db):
        from sqlalchemy import update

        from app.models.category import Category

        db.execute(
            update(Category).where(Category.id == category.id).values(is_active=False)
        )
        db.commit()
        return category

    @pytest.fixture
    def live_doc(self, client, admin_headers):
        return create_document(client, admin_headers, title="停用分类被引资料",
                               effective_from="2026-01-01")["id"]

    def _assert_validation_error(self, resp, ctx: str) -> None:
        assert resp.status_code == 400, f"{ctx}: 期望 400（VALIDATION_ERROR），实际 {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["error"]["code"] == "VALIDATION_ERROR", f"{ctx}: 错误码不符 {body}"
        assert "停用" in body["error"]["message"], f"{ctx}: 提示应说明分类已停用：{body['error']['message']}"

    def test_create_with_legacy_deleted_category(
        self, client, admin_headers, legacy_deleted_category
    ):
        resp = client.post("/api/v1/documents", headers=admin_headers, json={
            "title": "引用历史停用分类", "effective_from": "2026-01-01",
            "category_id": legacy_deleted_category.id,
        })
        self._assert_validation_error(resp, "创建资料")

    def test_update_to_legacy_deleted_category(
        self, client, admin_headers, legacy_deleted_category, live_doc
    ):
        resp = client.put(f"/api/v1/documents/{live_doc}", headers=admin_headers,
                          json={"category_id": legacy_deleted_category.id})
        self._assert_validation_error(resp, "修改资料")
        detail = client.get(f"/api/v1/documents/{live_doc}", headers=admin_headers).json()
        assert detail["category_id"] is None, "被拒的修改不得改变原分类"

    def test_bulk_with_legacy_deleted_category_no_partial_update(
        self, client, admin_headers, legacy_deleted_category, live_doc
    ):
        resp = client.post("/api/v1/documents/bulk", headers=admin_headers, json={
            "document_ids": [live_doc], "set_category_id": legacy_deleted_category.id,
        })
        self._assert_validation_error(resp, "批量整理")
        detail = client.get(f"/api/v1/documents/{live_doc}", headers=admin_headers).json()
        assert detail["category_id"] is None, "批量整理被拒后不得产生部分更新"


# ---------------------------------------------------------------------------
# 5) 分类删除：删除整棵子树；树内资料（含已归档）变未分类
# ---------------------------------------------------------------------------
class TestCategoryDeleteTreeSemantics:
    """2026-11-09 用户确认：删除=整棵子树；资料含已归档转未分类；
    资料/文件/附件/标签/通知/通知类型均不删除；不做历史分类名快照。"""

    def test_delete_parent_removes_subtree_unclassifies_docs_and_preserves_vocabularies(
        self, client, db, admin_headers, category_factory, tag_factory
    ):
        from app.models.audit import AuditLog
        from app.models.category import Category, document_tags
        from app.models.document import Document
        from app.models.notice import Notice, notice_documents

        parent = category_factory("删除语义-父")
        child = category_factory("删除语义-子", parent_id=parent.id)
        grandchild = category_factory("删除语义-孙", parent_id=child.id)
        sibling = category_factory("删除语义-旁支")
        tag = tag_factory("删除语义-保留标签")
        parent_id, parent_name = parent.id, parent.name
        subtree = {parent.id, child.id, grandchild.id}

        doc_parent = create_document(
            client, admin_headers, title="删除语义-父资料",
            category_id=parent.id, tag_ids=[tag.id],
        )["id"]
        doc_child = create_document(
            client, admin_headers, title="删除语义-子资料", category_id=child.id,
        )["id"]
        doc_grand = create_document(
            client, admin_headers, title="删除语义-孙资料", category_id=grandchild.id,
        )["id"]
        doc_archived = create_document(
            client, admin_headers, title="删除语义-归档资料", category_id=child.id,
        )["id"]
        notice = create_notice(
            client, admin_headers, title="删除语义-通知保留", content="正文",
            notice_type="发布", document_ids=[doc_parent], target={"scope": "all"},
        )
        resp = client.request(
            "DELETE", f"/api/v1/documents/{doc_archived}",
            headers=admin_headers, json={"reason": "先归档"},
        )
        assert resp.status_code == 200, resp.text

        resp = client.delete(f"/api/v1/categories/{parent_id}", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert set(body["deleted_category_ids"]) == subtree
        assert body["deleted_category_count"] == 3
        assert body["unclassified_document_count"] == 4

        db.expire_all()
        assert db.get(Category, sibling.id) is not None, "子树外的分类必须保留"
        for cid in subtree:
            assert db.get(Category, cid) is None, f"分类 {cid} 应被物理删除"
        for doc_id in (doc_parent, doc_child, doc_grand, doc_archived):
            doc = db.get(Document, doc_id)
            assert doc is not None, f"资料 {doc_id} 不得被删除"
            assert doc.category_id is None, f"资料 {doc_id} 应变为未分类"
        archived = db.get(Document, doc_archived)
        assert archived.archived_at is not None, "已归档资料的归档状态应保留"

        tag_links = db.execute(
            select(func.count()).select_from(document_tags).where(
                document_tags.c.document_id == doc_parent
            )
        ).scalar_one()
        assert tag_links == 1, "资料的标签关联必须保留"

        notice_row = db.execute(
            select(Notice).where(Notice.id == notice["id"])
        ).scalars().one()
        assert notice_row.notice_type == "发布", "通知类型不得被分类删除修改"
        notice_links = db.execute(
            select(func.count()).select_from(notice_documents).where(
                notice_documents.c.document_id == doc_parent
            )
        ).scalar_one()
        assert notice_links == 1, "通知-资料关联必须保留"

        log = db.execute(
            select(AuditLog).where(
                AuditLog.action == "delete_category",
                AuditLog.object_id == parent_id,
            )
        ).scalars().one_or_none()
        assert log is not None, "分类删除必须写审计"
        detail = json.loads(log.detail)
        assert detail["deleted_category_count"] == 3
        assert detail["unclassified_document_count"] == 4
        assert set(detail["deleted_category_ids"]) == subtree

        resp = client.post(
            "/api/v1/categories", headers=admin_headers, json={"name": parent_name}
        )
        assert resp.status_code == 201, "删除后名字应立即释放，可重建同名"

    def test_delete_child_removes_only_its_subtree(
        self, client, db, admin_headers, category_factory
    ):
        from app.models.category import Category
        from app.models.document import Document

        parent = category_factory("删除语义-保留父")
        child = category_factory("删除语义-待删子", parent_id=parent.id)
        grandchild = category_factory("删除语义-待删孙", parent_id=child.id)
        sibling = category_factory("删除语义-保留旁支", parent_id=parent.id)
        parent_id, child_id = parent.id, child.id
        subtree = {child.id, grandchild.id}

        doc_parent = create_document(
            client, admin_headers, title="旁支外父资料", category_id=parent_id,
        )["id"]
        doc_sibling = create_document(
            client, admin_headers, title="旁支资料", category_id=sibling.id,
        )["id"]
        doc_child = create_document(
            client, admin_headers, title="待删子资料", category_id=child_id,
        )["id"]
        doc_grand = create_document(
            client, admin_headers, title="待删孙资料", category_id=grandchild.id,
        )["id"]

        resp = client.delete(f"/api/v1/categories/{child_id}", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body["deleted_category_ids"]) == subtree
        assert body["deleted_category_count"] == 2
        assert body["unclassified_document_count"] == 2

        db.expire_all()
        assert db.get(Category, parent_id) is not None
        assert db.get(Category, sibling.id) is not None
        for cid in subtree:
            assert db.get(Category, cid) is None
        assert db.get(Document, doc_parent).category_id == parent_id
        assert db.get(Document, doc_sibling).category_id == sibling.id
        assert db.get(Document, doc_child).category_id is None
        assert db.get(Document, doc_grand).category_id is None

    def test_delete_parent_removes_inactive_child_too(
        self, client, db, admin_headers, category_factory
    ):
        from sqlalchemy import update

        from app.models.category import Category

        parent = category_factory("删除语义-含停用子")
        child = category_factory("删除语义-停用子", parent_id=parent.id)
        parent_id, child_id = parent.id, child.id
        db.execute(
            update(Category).where(Category.id == child_id).values(is_active=False)
        )
        db.commit()

        resp = client.delete(f"/api/v1/categories/{parent_id}", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted_category_count"] == 2
        db.expire_all()
        assert db.get(Category, parent_id) is None
        assert db.get(Category, child_id) is None

    def test_delete_missing_category_returns_404(self, client, admin_headers):
        resp = client.delete("/api/v1/categories/999999999", headers=admin_headers)
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == "NOT_FOUND"

    def test_non_admin_cannot_delete_category(
        self, client, admin_headers, user_headers, category_factory
    ):
        cat = category_factory("删除语义-普通用户不可删")
        resp = client.delete(f"/api/v1/categories/{cat.id}", headers=user_headers)
        assert resp.status_code == 403, resp.text
        assert client.get("/api/v1/categories", headers=admin_headers).status_code == 200


# ---------------------------------------------------------------------------
# 6) 软删除实体不占用名字：创建/更新重名检查排除已删除
# ---------------------------------------------------------------------------
class TestSoftDeletedDoesNotBlockCreation:
    """已删除（不可见）实体不得占用唯一名（死角回归：曾导致同名创建被拒）。"""

    def test_deleted_category_name_reusable(self, client, admin_headers, category):
        resp = client.delete(f"/api/v1/categories/{category.id}", headers=admin_headers)
        assert resp.status_code == 200
        resp = client.post("/api/v1/categories", headers=admin_headers,
                           json={"name": category.name})
        assert resp.status_code == 201, f"已删除分类不应占用名字: {resp.text}"

    def test_deleted_department_name_reusable(self, client, admin_headers,
                                              department_factory):
        dep = department_factory("不变量-可复用部门")
        resp = client.request("DELETE", f"/api/v1/departments/{dep.id}",
                              headers=admin_headers)
        assert resp.status_code == 200
        resp = client.post("/api/v1/departments", headers=admin_headers,
                           json={"name": dep.name})
        assert resp.status_code == 201, f"删除后部门名应立即释放: {resp.text}"

    def test_active_duplicate_still_rejected(self, client, admin_headers, category):
        """对照：未删除的同名分类仍正常拦截。"""
        resp = client.post("/api/v1/categories", headers=admin_headers,
                           json={"name": category.name})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "DUPLICATE"


# ---------------------------------------------------------------------------
# 7) 部门成员数口径：排除停用用户
# ---------------------------------------------------------------------------
class TestDepartmentMemberCountExcludesInactive:
    """member_count 只统计启用用户（与通知统计分母 §21 口径一致）。"""

    def test_member_count_excludes_deactivated_user(
        self, client, admin_headers, db, department, user_factory
    ):
        from app.models.user import user_departments

        u1 = user_factory("dept_cnt_u1", display_name="成员甲")
        u2 = user_factory("dept_cnt_u2", display_name="成员乙")
        for u in (u1, u2):
            db.execute(user_departments.insert().values(
                user_id=u.id, department_id=department.id))
        db.commit()

        def _count() -> int:
            items = client.get(
                "/api/v1/departments?with_member_count=1",
                headers=admin_headers,
            ).json()["items"]
            return next(i["member_count"] for i in items if i["id"] == department.id)

        assert _count() == 2, "启用时两个成员都应计入"
        client.delete(f"/api/v1/users/{u2.id}", headers=admin_headers)
        assert _count() == 1, "停用成员不应计入部门成员数"


# ---------------------------------------------------------------------------
# 8) 用户名占用：已删除账号给出可解释文案（语义保留：防冒用，不放开重建）
# ---------------------------------------------------------------------------
class TestUsernameOccupiedByDeletedAccount:
    def test_deleted_account_username_gives_clear_message(
        self, client, admin_headers, user_factory
    ):
        u = user_factory("occupied_name", display_name="被删账号")
        client.delete(f"/api/v1/users/{u.id}", headers=admin_headers)
        resp = client.post("/api/v1/users", headers=admin_headers, json={
            "username": "occupied_name", "password": "New@Pass123",
            "display_name": "重建者",
        })
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"]["code"] == "DUPLICATE"
        assert "已删除账号" in body["error"]["message"], \
            "文案应说明是被删除账号占用并给出出路"

    def test_deleted_subcategory_name_reusable(self, client, admin_headers,
                                               category_factory):
        """二级分类（parent_id 非空）软删除后同名重建 → 201（部分唯一索引回归）。"""
        parent = category_factory("不变量-父分类C")
        child = category_factory("不变量-子分类C1", parent_id=parent.id)
        resp = client.delete(f"/api/v1/categories/{child.id}", headers=admin_headers)
        assert resp.status_code == 200
        resp = client.post("/api/v1/categories", headers=admin_headers, json={
            "name": child.name, "parent_id": parent.id})
        assert resp.status_code == 201, f"已删除二级分类不应占用名字: {resp.text}"


# ---------------------------------------------------------------------------
# 9) 部门删除口径 + 恢复资料挂已删除分类的残存防护
# ---------------------------------------------------------------------------
class TestDepartmentDeleteAndRestoreResidue:
    """2026-11-09：部门删除不因成员而拒绝，成员变未分配部门；通知目标行摘除。"""

    def test_department_deletable_when_only_members_deactivated(
        self, client, admin_headers, db, department, user_factory
    ):
        from app.models.user import user_departments

        u = user_factory("dept_only_inactive", display_name="停用成员")
        db.execute(user_departments.insert().values(
            user_id=u.id, department_id=department.id))
        db.commit()
        client.delete(f"/api/v1/users/{u.id}", headers=admin_headers)  # 停用唯一成员

        # 列表显示 0 成员（已排除停用），删除必须放行——否则死锁复刻
        items = client.get("/api/v1/departments?with_member_count=1",
                           headers=admin_headers).json()["items"]
        assert next(i["member_count"] for i in items if i["id"] == department.id) == 0
        resp = client.request("DELETE", f"/api/v1/departments/{department.id}",
                              headers=admin_headers)
        assert resp.status_code == 200, f"成员全部停用后部门应可删除: {resp.text}"

    # 注：原「分类被删后资料恢复归未分类」用例已随物理删语义移除——
    # documents.category_id 存在 FK 约束（PRAGMA foreign_keys=ON），
    # 「资料引用不存在的分类」在数据库层不可能出现，场景不可达。


# ---------------------------------------------------------------------------
# 10) 用户删除（purge）：产品决策 2026-09——无需二次确认；内容引用拦截；
#     阅读快照随删、操作日志保留（user_id 置 NULL、username 文本仍在）
# ---------------------------------------------------------------------------
class TestPurgeUserAndDepartment:
    """用户删除边界：创建过内容（资料/通知）的账号拦截；其余痕迹可清理删除。"""

    def test_purge_clean_account_ok_and_name_released(
        self, client, admin_headers, user_factory
    ):
        u = user_factory("purge_clean")  # ORM 直插：未登录、无业务痕迹
        resp = client.delete(f"/api/v1/users/{u.id}/purge", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        # 行已删除、用户名释放可重建（无需停用、无需 confirm）
        resp = client.post("/api/v1/users", headers=admin_headers, json={
            "username": "purge_clean", "password": "Purge@Pass123",
            "display_name": "重建同名",
        })
        assert resp.status_code == 201, f"删除后用户名应释放: {resp.text}"

    def test_purge_active_account_ok(self, client, admin_headers, user_factory):
        """启用中的账号（未创建内容）可直接删除，无需先停用。"""
        u = user_factory("purge_active")
        resp = client.delete(f"/api/v1/users/{u.id}/purge", headers=admin_headers)
        assert resp.status_code == 200, resp.text

    def test_purge_blocked_by_document_reference(self, client, admin_headers,
                                                 user_factory, db):
        u = user_factory("purge_docref")
        doc_id = create_document(client, admin_headers, title="purge 引用资料",
                                 effective_from="2026-01-01")["id"]
        from app.models.document import Document
        d = db.get(Document, doc_id)
        d.created_by = u.id
        db.commit()
        resp = client.delete(f"/api/v1/users/{u.id}/purge", headers=admin_headers)
        assert resp.status_code == 409
        assert "资料" in resp.json()["error"]["message"]

    def test_purge_keeps_audit_with_null_user_and_text(
        self, client, admin_headers, user_factory, headers_for, db
    ):
        """有操作日志（含登录）的账号可删；删除后日志行保留、user_id 置空、用户名文本仍在。"""
        u = user_factory("purge_logged", password="Logged@123")
        headers_for(u, password="Logged@123")  # 登录一次 → 产生操作日志
        from app.models.audit import AuditLog
        uid = u.id
        before = db.execute(
            select(func.count(AuditLog.id)).where(AuditLog.user_id == uid)
        ).scalar_one()
        assert before > 0, "前置：登录应产生操作日志"

        resp = client.delete(f"/api/v1/users/{uid}/purge", headers=admin_headers)
        assert resp.status_code == 200, resp.text

        after = db.execute(
            select(func.count(AuditLog.id)).where(AuditLog.user_id.is_(None))
        ).scalar_one()
        names = db.execute(
            select(AuditLog.username).where(AuditLog.username == "purge_logged")
        ).scalars().all()
        assert after >= before, "日志行不得被删除（§25 留痕）"
        assert names, "删除后日志中的用户名文本必须保留"

    def test_purge_clears_notice_read_rows(self, client, admin_headers,
                                           user_factory, db):
        """有通知阅读快照的账号可删；快照随账号清除，通知统计同步收敛。"""
        u = user_factory("purge_reader")
        resp = client.post("/api/v1/notices", headers=admin_headers, json={
            "title": "purge 阅读门禁通知", "content": "正文", "notice_type": "发布",
            "target": {"scope": "user", "user_ids": [u.id]},
        })
        nid = resp.json()["id"]  # 2026-11-04：创建即发布
        stats = client.get(f"/api/v1/notices/{nid}/statistics",
                           headers=admin_headers).json()
        assert stats["target_user_count"] == 1

        resp = client.delete(f"/api/v1/users/{u.id}/purge", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        stats = client.get(f"/api/v1/notices/{nid}/statistics",
                           headers=admin_headers).json()
        assert stats["target_user_count"] == 0, "阅读快照已随账号清除"

    def test_delete_department_with_active_member_unassigns_user(
        self, client, admin_headers, department_factory, db, user_factory
    ):
        from app.models.user import user_departments

        # 2026-11-09：有启用成员不再拒绝；成员关系解除，用户变未分配部门
        dep = department_factory("删除部门-启用中")
        u0 = user_factory("dept_active_member")
        db.execute(user_departments.insert().values(
            user_id=u0.id, department_id=dep.id))
        db.commit()
        resp = client.request("DELETE", f"/api/v1/departments/{dep.id}",
                              headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["affected_member_count"] == 1
        detail = client.get(f"/api/v1/users/{u0.id}", headers=admin_headers).json()
        assert detail["department_ids"] == []

        # 部门名释放，可重建
        resp = client.post("/api/v1/departments", headers=admin_headers,
                           json={"name": dep.name})
        assert resp.status_code == 201, "删除后部门名应释放"

        # 空部门删除仍成功
        dep3 = department_factory("删除部门-空")
        resp = client.request("DELETE", f"/api/v1/departments/{dep3.id}",
                              headers=admin_headers)
        assert resp.status_code == 200

    def test_purge_user_with_backup_and_file_history_ok(
        self, client, admin_headers, user_factory, db
    ):
        """运维历史（备份记录/回收站/上传记录）引用该用户时，purge 必须成功。

        2026-10-29 回归守卫。此前 purge_user 只清了 NoticeRead/NoticeTarget/
        AuditLog/user_departments，而 backup_records.created_by、recycle_bin.*、
        file_records.uploaded_by 仍是 FK NO ACTION → 删行抛 IntegrityError → 500。
        这些都不是内容责任链（不产生资料/通知），应置 NULL 而非阻断删除；
        备份集与回收站条目本身必须保留（§28/§26）。
        """
        from datetime import datetime

        from app.models.backup import BackupRecord
        from app.models.document import FileRecord

        u = user_factory("purge_ops_history")
        db.add(BackupRecord(
            backup_type="full", backup_path="x", manifest_path="x", size_bytes=1,
            status="completed", source="manual", created_by=u.id,
            created_at=datetime.now(),
        ))
        db.add(FileRecord(
            owner_type="document", owner_id=1, original_filename="a.pdf",
            internal_name="purge_ops_a.pdf", file_type="pdf", extension=".pdf",
            mime_type="application/pdf", size_bytes=1, sha256="0" * 64,
            storage_rel_path="documents/purge_ops_a.pdf", uploaded_by=u.id,
            uploaded_at=datetime.now(), created_at=datetime.now(),
        ))
        db.commit()
        uid = u.id

        resp = client.delete(f"/api/v1/users/{uid}/purge", headers=admin_headers)
        assert resp.status_code == 200, f"应成功删除，实际: {resp.status_code} {resp.text}"

        # 运维历史本身保留，只是归属人置空
        assert db.execute(
            select(func.count(BackupRecord.id))
        ).scalar_one() == 1, "备份记录不得随用户删除"
        fr = db.execute(
            select(FileRecord).where(FileRecord.internal_name == "purge_ops_a.pdf")
        ).scalar_one()
        assert fr.uploaded_by is None

    def test_purge_user_referenced_by_document_target_ok(
        self, client, admin_headers, user_factory, db
    ):
        """被资料可见范围引用的用户必须可删（2026-11-16 验收实测缺陷）。

        `document_targets.user_id` 是 FK NO ACTION，而 purge_user 当时只清了
        NoticeRead/NoticeTarget/AuditLog/BackupRecord/RecycleBin/FileRecord/
        user_departments，漏了它 → DELETE 抛 IntegrityError → 500，
        AGENTS §26.1「用户物理删除点击即删」直接断裂。

        预期：删除成功；资料保留；该用户的授权行被移除；其他可见对象不受影响。
        """
        from app.models.document import Document, DocumentTarget

        victim = user_factory("purge_doc_target", display_name="被引用者")
        keeper = user_factory("keep_doc_target", display_name="保留者")
        resp = client.post("/api/v1/documents", headers=admin_headers, json={
            "title": "引用两个用户的资料",
            "effective_from": "2026-01-01",
            "audience_scope": "user",
            "audience_user_ids": [victim.id, keeper.id],
        })
        assert resp.status_code == 201, resp.text
        doc_id = resp.json()["id"]
        victim_id = victim.id

        purge = client.delete(f"/api/v1/users/{victim_id}/purge", headers=admin_headers)
        assert purge.status_code == 200, f"应成功删除，实际: {purge.status_code} {purge.text}"

        assert db.get(Document, doc_id) is not None, "资料本身不得被删除"
        remaining = db.execute(
            select(DocumentTarget.user_id).where(DocumentTarget.document_id == doc_id)
        ).scalars().all()
        assert victim_id not in remaining, "被删用户的授权行应被移除"
        assert keeper.id in remaining, "其他可见对象不应受影响"

    def test_delete_department_referenced_by_notice_target(
        self, client, admin_headers, department_factory, user_factory, db
    ):
        """被通知当作阅读对象的部门必须可删（§26.1 点击即删）。

        2026-10-29 回归守卫。notice_targets.department_id 是 FK NO ACTION，
        此前 delete_department 只清 user_departments → IntegrityError → 500，
        且 §26.1 的「物理删除免二次确认」路径直接断裂。
        预期：删除成功，对应的通知目标行被摘除，通知本身保留。
        """
        from app.models.notice import NoticeTarget

        dep = department_factory("被通知引用的部门")
        # 2026-11-04：创建即发布要求阅读对象非空 → 部门里必须有成员。
        # 2026-11-09：成员保持启用；删除部门后成员应变为未分配部门。
        member = user_factory("部门内成员", role="user", password="user1234",
                              department_ids=[dep.id])
        resp = client.post("/api/v1/notices", headers=admin_headers, json={
            "title": "定向部门通知", "content": "正文", "notice_type": "发布",
            "target": {"scope": "department", "department_ids": [dep.id]},
        })
        assert resp.status_code == 201, resp.text
        nid = resp.json()["id"]
        target_before = db.execute(
            select(func.count(NoticeTarget.id)).where(
                NoticeTarget.department_id == dep.id)
        ).scalar_one()
        assert target_before > 0, "前置：通知应已建立部门目标"
        # 资料可见范围也指定该部门，验证 document_targets FK 清理
        from app.models.document import Document, DocumentTarget
        doc = create_document(client, admin_headers, title="部门可见资料",
                              effective_from="2026-01-01")
        # create_document helper 不暴露 audience；用 PUT 设置部门可见
        put = client.put(f"/api/v1/documents/{doc['id']}", headers=admin_headers, json={
            "audience_scope": "department",
            "audience_department_ids": [dep.id],
        })
        assert put.status_code == 200, put.text
        doc_target_before = db.execute(
            select(func.count(DocumentTarget.id)).where(
                DocumentTarget.department_id == dep.id)
        ).scalar_one()
        assert doc_target_before > 0, "前置：资料应已建立部门目标"
        resp = client.request("DELETE", f"/api/v1/departments/{dep.id}",
                              headers=admin_headers)
        assert resp.status_code == 200, f"应成功删除，实际: {resp.status_code} {resp.text}"
        assert resp.json()["affected_member_count"] == 1
        member_detail = client.get(f"/api/v1/users/{member.id}", headers=admin_headers).json()
        assert member_detail["department_ids"] == []

        target_after = db.execute(
            select(func.count(NoticeTarget.id)).where(
                NoticeTarget.department_id == dep.id)
        ).scalar_one()
        assert target_after == 0, "该部门的通知目标行应被摘除"
        doc_target_after = db.execute(
            select(func.count(DocumentTarget.id)).where(
                DocumentTarget.department_id == dep.id)
        ).scalar_one()
        assert doc_target_after == 0, "该部门的资料可见目标行应被摘除"
        assert db.get(Document, doc["id"]) is not None, "资料本身不得被删除"
        # 通知本身保留
        assert client.get(f"/api/v1/notices/{nid}",
                          headers=admin_headers).status_code == 200


# ---------------------------------------------------------------------------
# 11) 分类生命周期补全：恢复 + 彻底删除（坟场治理）
# ---------------------------------------------------------------------------
class TestCategoryDirectDelete:
    """分类删除=直接物理删（2026-09-03 产品决策）：无软删/恢复/彻底删除三段式，
    「彻底删除」端点一并移除。"""

    def test_delete_is_direct_without_confirm(self, client, admin_headers, category_factory):
        cat = category_factory("直删-零引用分类")
        resp = client.delete(f"/api/v1/categories/{cat.id}", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True
        assert resp.json()["name"] == cat.name
        assert resp.json()["deleted_category_count"] == 1
        assert resp.json()["unclassified_document_count"] == 0

    def test_purge_endpoint_removed(self, client, admin_headers, category):
        resp = client.request("DELETE", f"/api/v1/categories/{category.id}/purge",
                              headers=admin_headers, json={"confirm": True})
        assert resp.status_code == 404, "彻底删除端点已随三段式语义一并移除"

    def test_list_only_returns_active(self, client, admin_headers, category_factory, db):
        """列表只返回启用分类；历史软删行（迁移保留的有引用行）不可见。"""
        from sqlalchemy import update

        from app.models.category import Category

        cat = category_factory("直删-隐藏校验")
        db.execute(
            update(Category).where(Category.id == cat.id).values(is_active=False)
        )
        db.commit()
        items = client.get("/api/v1/categories", headers=admin_headers).json()["items"]

        def walk(nodes):
            for n in nodes:
                yield n
                yield from walk(n.get("children") or [])

        assert all(n["id"] != cat.id for n in walk(items)), "停用分类不得出现在列表"

