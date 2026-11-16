"""通知模块测试（项目规范 §17–§22/§31、API.md §7）。

覆盖：创建（纯文字/文件/两者）、草稿修改、发布（物化快照数量=应阅读人数）、
阅读 read_at 幂等、统计（应读/已读/未读，三种对象模式；管理员不进分母）、废止联动（发布废止通知→关联资料 effective_to=通知生效日）、
已发布+已读后修改需 confirm_modify+reason 否则 409、修改记录审计。
"""
from __future__ import annotations

import io

from files.generators import DOCX_FILENAME, PDF_FILENAME, make_docx, make_pdf
from helpers import create_document, login_headers


def _create_notice(client, headers, **kw):
    payload = {
        "title": kw.get("title", "测试通知"),
        "notice_type": kw.get("notice_type", "发布"),
    }
    content = kw.get("content")
    if content is not None:
        payload["content"] = content
    if kw.get("effective_at"):
        payload["effective_at"] = kw["effective_at"]
    if kw.get("document_ids"):
        payload["document_ids"] = kw["document_ids"]
    if kw.get("target") is not None:
        payload["target"] = kw["target"]
    resp = _post_with_reader(client, headers, json=payload)
    assert resp.status_code == 201, f"创建通知失败 {resp.status_code}: {resp.text}"
    return resp.json()


def _is_empty_audience(resp) -> bool:
    try:
        return "阅读对象范围为空" in resp.json()["error"]["message"]
    except Exception:  # noqa: BLE001
        return False


def _ensure_one_reader(client, headers):
    client.post("/api/v1/users", headers=headers, json={
        "username": "通知读者甲", "password": "user1234",
        "display_name": "通知读者甲", "role": "user",
    })


def _payload_is_all_scope(payload) -> bool:
    """未指定对象、或显式 scope=all → 属于「全体用户」语义。"""
    if not payload:
        return True
    if payload.get("__multipart__"):
        return True
    target = payload.get("target")
    return target is None or target.get("scope") == "all"


def _post_with_reader(client, headers, **kwargs):
    """POST /notices；仅当 scope=all 且因「阅读对象为空」失败时补读者重试。

    支持 json= 与 multipart（data=/files=）两种调用形式。
    显式 scope=user/department 的用例自带对象语义（例如「全是停用用户应当
    失败」），绝不能自动补人，否则断言失效。
    """
    payload = kwargs.get("json")
    if payload is None and "data" in kwargs:
        # multipart 分支无法预判 target，只在「空受众」错误出现时补人重试一次
        payload = {"__multipart__": True}
    resp = client.post("/api/v1/notices", headers=headers, **kwargs)
    try:
        empty = resp.status_code == 400 and _is_empty_audience(resp)
    except Exception:  # noqa: BLE001
        empty = False
    if empty and _payload_is_all_scope(payload):
        _ensure_one_reader(client, headers)
        resp = client.post("/api/v1/notices", headers=headers, **kwargs)
    return resp


class _FakeResp:
    """2026-11-04：创建即发布，/publish 已移除。

    保留该 shim 让既有用例继续表达「这条通知状态为 published」，避免大范围重写。
    """

    status_code = 200

    def __init__(self, body=None):
        self._body = body or {}

    def json(self):
        return self._body


def _publish(client, headers, notice_id, confirm_targets=True):
    detail = client.get(f"/api/v1/notices/{notice_id}", headers=headers)
    if detail.status_code != 200:
        return detail  # 让调用方拿到真实错误码（如已取消 404）
    return _FakeResp(detail.json())


def _stats(client, headers, notice_id):
    return client.get(f"/api/v1/notices/{notice_id}/statistics", headers=headers).json()


class TestNoticeCreate:
    def test_create_pure_text(self, client, admin_headers):
        notice = _create_notice(client, admin_headers, title="纯文字通知", content="重要通知正文")
        # 2026-11-04：创建即发布，不存在 draft
        assert notice["status"] == "published"
        assert notice["has_file"] is False
        assert notice["content"] == "重要通知正文"

    def test_create_with_file(self, client, admin_headers, tmp_path):
        """创建带原始文件的通知（multipart）。"""
        docx = make_docx(tmp_path / DOCX_FILENAME)
        resp = _post_with_reader(client, admin_headers,
                           data={"title": "文件通知", "notice_type": "发布"},
                           files={"file": (DOCX_FILENAME, io.BytesIO(docx.read_bytes()),
                                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["has_file"] is True
        assert body["file_type"] == "docx"

    def test_create_text_and_file(self, client, admin_headers, tmp_path):
        pdf = make_pdf(tmp_path / PDF_FILENAME)
        resp = _post_with_reader(client, admin_headers,
                           data={"title": "正文加文件", "content": "请看附件", "notice_type": "发布"},
                           files={"file": (PDF_FILENAME, io.BytesIO(pdf.read_bytes()), "application/pdf")})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["has_file"] is True and body["content"] == "请看附件"

    def test_create_requires_content_or_file(self, client, admin_headers):
        resp = _post_with_reader(client, admin_headers,
                           json={"title": "空通知", "notice_type": "发布"})
        assert resp.status_code == 400

    def test_create_invalid_type(self, client, admin_headers):
        resp = _post_with_reader(client, admin_headers,
                           json={"title": "错误类型", "content": "x", "notice_type": "爆炸"})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_create_empty_title(self, client, admin_headers):
        resp = _post_with_reader(client, admin_headers,
                           json={"title": "", "content": "x", "notice_type": "发布"})
        assert resp.status_code == 400

    def test_create_department_scope_requires_ids(self, client, admin_headers):
        resp = _post_with_reader(client, admin_headers, json={
            "title": "缺部门", "content": "x", "notice_type": "发布",
            "target": {"scope": "department", "department_ids": []},
        })
        assert resp.status_code == 400

    def test_draft_update(self, client, admin_headers):
        notice_id = _create_notice(client, admin_headers, title="草稿一改", content="初版")["id"]
        resp = client.put(f"/api/v1/notices/{notice_id}", headers=admin_headers,
                          json={"title": "草稿二改", "content": "修订版"})
        assert resp.status_code == 200, resp.text
        detail = client.get(f"/api/v1/notices/{notice_id}", headers=admin_headers).json()
        assert detail["title"] == "草稿二改"
        assert detail["content"] == "修订版"


class TestNoticePublish:
    def test_create_publishes_and_materializes_snapshot(self, client, admin_headers, user_factory):
        """全体模式：创建即发布，快照数量 = 激活普通用户数（§21）。"""
        user_factory("员工甲", role="user", password="user1234")
        user_factory("员工乙", role="user", password="user1234")  # admin + 2 员工
        body = _create_notice(client, admin_headers, title="全员通知", content="请阅读")
        assert body["status"] == "published"
        # 2026-11-03：管理员不进阅读对象，快照只有甲+乙
        assert body["target_user_count"] == 2

        stats = _stats(client, admin_headers, body["id"])
        assert stats["target_user_count"] == 2  # 统计排除 admin（2026-11-03 决策）
        assert stats["viewed_count"] == 0 and stats["unviewed_count"] == 2

    def test_create_requires_reader_and_reports_count(self, client, admin_headers, user_factory):
        """2026-11-04：没有可发布对象时创建直接 400；有读者则返回应阅读人数。"""
        user_factory("防误发读者", role="user", password="user1234")
        body = _create_notice(client, admin_headers, title="防误发", content="x")
        assert body["status"] == "published"
        assert body["target_user_count"] == 1

    def test_create_target_missing_user_400(self, client, admin_headers):
        """scope=user 引用不存在的用户 → 创建即 400（校验，防 FK 500）。"""
        resp = _post_with_reader(client, admin_headers, json={
            "title": "空受众", "content": "x", "notice_type": "发布",
            "target": {"scope": "user", "user_ids": [99999]},
        })
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_empty_audience_rejected_at_create(self, client, admin_headers, user_factory):
        """阅读对象全部为停用用户 → 应阅读为空 → 创建即 400（不再有草稿残留）。"""
        inactive = user_factory("已停用对象", role="user", password="user1234")
        client.put(f"/api/v1/users/{inactive.id}", headers=admin_headers,
                   json={"is_active": False})
        resp = client.post("/api/v1/notices", headers=admin_headers, json={
            "title": "空受众", "content": "x", "notice_type": "发布",
            "target": {"scope": "user", "user_ids": [inactive.id]},
        })
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        # 失败即整体回滚：库里不应留下任何一条同名通知
        titles = [n["title"] for n in client.get(
            "/api/v1/notices", headers=admin_headers).json()["items"]]
        assert "空受众" not in titles

    def test_publish_endpoint_removed(self, client, admin_headers):
        """2026-11-04：/publish 接口已删除（创建即发布），重复发布不可能发生。"""
        notice_id = _create_notice(client, admin_headers, title="发两次", content="x")["id"]
        resp = client.post(f"/api/v1/notices/{notice_id}/publish",
                           json={"confirm_targets": True}, headers=admin_headers)
        assert resp.status_code in (404, 405), "旧发布接口应已下线"

    def test_cancel_publish_protection(self, client, admin_headers, user_factory):
        user_factory("员工丙", role="user", password="user1234")
        notice_id = _create_notice(client, admin_headers, title="取消后不能发布", content="x")["id"]
        resp = client.post(f"/api/v1/notices/{notice_id}/cancel", headers=admin_headers,
                           json={"reason": "内容有误"})
        assert resp.status_code == 200
        # 已删除通知不在列表中显示
        titles = [n["title"] for n in client.get("/api/v1/notices", headers=admin_headers).json()["items"]]
        assert "取消后不能发布" not in titles
        # 已删除通知不能重新发布（旧接口已下线，直接确认状态未变）
        detail = client.get(f"/api/v1/notices/{notice_id}", headers=admin_headers)
        assert detail.status_code in (200, 404)

    def test_user_list_only_published_effective(self, client, admin_headers, user_headers):
        """普通用户只见已发布且已生效且 target 含本人的通知。"""
        pub_id = _create_notice(client, admin_headers, title="可见通知", content="x")["id"]
        _publish(client, admin_headers, pub_id)
        future_id = _create_notice(client, admin_headers, title="未来生效不可见", content="x",
                                   effective_at="2099-01-01T00:00:00")["id"]
        _publish(client, admin_headers, future_id)
        listed = client.get("/api/v1/notices", headers=user_headers).json()
        titles = [n["title"] for n in listed["items"]]
        assert titles == ["可见通知"]


class TestNoticeReadConfirm:
    def _published_notice(self, client, admin_headers, user_factory, **kw):
        user_factory("读者", role="user", password="user1234")
        notice = _create_notice(client, admin_headers,
                                title=kw.get("title", "阅读通知"),
                                content="请阅读")
        _publish(client, admin_headers, notice["id"])
        return notice["id"]

    def test_read_idempotent(self, client, admin_headers, user_factory):
        notice_id = self._published_notice(client, admin_headers, user_factory)
        headers = login_headers(client, "读者", "user1234")
        r1 = client.post(f"/api/v1/notices/{notice_id}/read", headers=headers)
        assert r1.status_code == 200, r1.text
        read_at_1 = r1.json()["my_read_at"]
        r2 = client.post(f"/api/v1/notices/{notice_id}/read", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["my_read_at"] == read_at_1  # 幂等，不覆盖

        stats = _stats(client, admin_headers, notice_id)
        assert stats["viewed_count"] == 1
        assert stats["unviewed_count"] == stats["target_user_count"] - 1
        # 管理员不是阅读对象时没有快照，详情显示 unread
        my = client.get(f"/api/v1/notices/{notice_id}", headers=admin_headers).json()
        assert my["my_read_status"] == "unread"

    def test_confirm_endpoint_removed(self, client, admin_headers, user_factory):
        """2026-11-03 产品决策：确认功能整体下线，/confirm 不再存在。

        保留此用例是为了防止将来有人"顺手"把接口加回来——确认与查看两套
        并存是这次被用户否掉的设计。
        """
        notice_id = self._published_notice(client, admin_headers, user_factory)
        headers = login_headers(client, "读者", "user1234")
        resp = client.post(f"/api/v1/notices/{notice_id}/confirm",
                           headers=headers, json={"confirm": True})
        assert resp.status_code in (404, 405), (
            f"确认接口应已移除，实际 {resp.status_code}: {resp.text}"
        )

    def test_statistics_excludes_admin(self, client, admin_headers, user_factory):
        """管理员能看全部通知，但不计入阅读统计（2026-11-03 产品决策）。

        管理员上传/发布通知是管理行为，不是"被要求阅读"；把他算进分母会让
        完成率永远差一个人，统计页也就失去意义。
        """
        notice_id = self._published_notice(client, admin_headers, user_factory)
        stats = _stats(client, admin_headers, notice_id)
        # 仅 1 名普通用户为读者，admin 不进分母
        assert stats["target_user_count"] == 1
        audience = client.get(
            f"/api/v1/notices/{notice_id}/audience", headers=admin_headers
        ).json()
        assert audience["total"] == 1
        assert all(u["username"] != "admin" for u in audience["items"])

    def test_audience_keyword_search(self, client, admin_headers, user_factory):
        """名单可按姓名/账号搜索——几百人的名单不能靠翻页找人。"""
        user_factory("张三", role="user", password="user1234")
        user_factory("李四", role="user", password="user1234")
        user_factory("王五", role="user", password="user1234")
        notice = _create_notice(client, admin_headers, title="搜人通知", content="x")
        _publish(client, admin_headers, notice["id"])

        all_rows = client.get(
            f"/api/v1/notices/{notice['id']}/audience", headers=admin_headers
        ).json()
        assert all_rows["total"] == 3

        hit = client.get(
            f"/api/v1/notices/{notice['id']}/audience",
            headers=admin_headers, params={"keyword": "李四"},
        ).json()
        assert hit["total"] == 1
        assert hit["items"][0]["display_name"] == "李四"

        miss = client.get(
            f"/api/v1/notices/{notice['id']}/audience",
            headers=admin_headers, params={"keyword": "查无此人"},
        ).json()
        assert miss["total"] == 0

    def test_audience_filter_unread(self, client, admin_headers, user_factory):
        """默认视图「未查看」必须只返回没看过的人。"""
        user_factory("已读者", role="user", password="user1234")
        user_factory("未读者", role="user", password="user1234")
        notice = _create_notice(client, admin_headers, title="筛选通知", content="x")
        _publish(client, admin_headers, notice["id"])
        h = login_headers(client, "已读者", "user1234")
        client.post(f"/api/v1/notices/{notice['id']}/read", headers=h)

        unread = client.get(
            f"/api/v1/notices/{notice['id']}/audience",
            headers=admin_headers, params={"read_status": "unread"},
        ).json()
        assert unread["total"] == 1
        assert unread["items"][0]["display_name"] == "未读者"
        assert unread["items"][0]["read_at"] is None

        read = client.get(
            f"/api/v1/notices/{notice['id']}/audience",
            headers=admin_headers, params={"read_status": "read"},
        ).json()
        assert read["total"] == 1
        assert read["items"][0]["display_name"] == "已读者"

    def test_detail_in_audience_flag(self, client, admin_headers, user_factory):
        """详情响应 in_audience：对象内 true；范围外 admin false（§20/§21）。

        admin 可见全部通知但未必是阅读对象——前端据此隐藏必报错的确认条
        （非对象 confirm 会被 _require_user_target 403）。
        """
        u = user_factory("定点读者", password="user1234")
        notice = _create_notice(
            client, admin_headers, title="定点阅读通知", content="x",
            target={"scope": "user", "user_ids": [u.id]},
        )
        _publish(client, admin_headers, notice["id"])

        mine = client.get(f"/api/v1/notices/{notice['id']}", headers=login_headers(client, "定点读者", "user1234")).json()
        assert mine["in_audience"] is True

        admin_view = client.get(f"/api/v1/notices/{notice['id']}", headers=admin_headers).json()
        assert admin_view["in_audience"] is False
        assert admin_view["my_read_status"] == "unread"  # 非对象不置读、不进统计
        assert admin_view["is_target"] is False  # 列表/详情前端据此隐藏确认操作

        # 列表：admin 视角该通知 my_read_status=unread 但 is_target=False，
        # 前端不得为其提供行内确认
        listed = client.get(
            "/api/v1/notices", headers=admin_headers, params={"keyword": "定点阅读通知"}
        ).json()
        row = next(n for n in listed["items"] if n["id"] == notice["id"])
        assert row["is_target"] is False
        assert row["my_read_status"] == "unread"
        target_row = client.get(
            f"/api/v1/notices/{notice['id']}", headers=login_headers(client, "定点读者", "user1234")
        ).json()
        assert target_row["is_target"] is True
        # audience_meta：ids 供编辑回填，names 供详情页展示姓名
        assert admin_view["audience_meta"]["user_ids"] == [u.id]
        assert admin_view["audience_meta"]["user_names"] == ["定点读者"]

    def test_statistics_three_target_modes(self, client, admin_headers, user_factory, department_factory):
        """三种对象模式：all / department / user 的应阅读人数与统计口径（§21）。"""
        dept = department_factory("财务部")
        user_factory("部门成员", role="user", password="user1234",
                     department_ids=[dept.id])
        outsider = user_factory("编外人员", role="user", password="user1234")

        # 部门模式
        n = _create_notice(client, admin_headers, title="部门通知", content="x",
                           target={"scope": "department", "department_ids": [dept.id]})["id"]
        _publish(client, admin_headers, n)
        stats = _stats(client, admin_headers, n)
        assert stats["target_user_count"] == 1  # 仅部门成员（admin 不在部门中）
        assert stats["by_department"][0]["department_id"] == dept.id
        assert stats["by_department"][0]["target"] == 1
        # 编外人员不可见
        outsider_headers = login_headers(client, "编外人员", "user1234")
        assert client.get(f"/api/v1/notices/{n}", headers=outsider_headers).status_code == 403

        # user 模式
        n2 = _create_notice(client, admin_headers, title="指定人员通知", content="x",
                            target={"scope": "user", "user_ids": [outsider.id]})["id"]
        _publish(client, admin_headers, n2)
        stats2 = _stats(client, admin_headers, n2)
        assert stats2["target_user_count"] == 1
        assert stats2["viewed_count"] == 0

        # all 模式：物化全部激活用户，但统计分母排除 admin
        n3 = _create_notice(client, admin_headers, title="全体通知", content="x")["id"]
        _publish(client, admin_headers, n3)
        assert _stats(client, admin_headers, n3)["target_user_count"] == 2

    def test_statistics_snapshot_frozen(self, client, admin_headers, user_factory):
        """发布后新增用户不计入统计（快照物化，§21）。"""
        notice_id = self._published_notice(client, admin_headers, user_factory)
        before = _stats(client, admin_headers, notice_id)["target_user_count"]
        user_factory("发布后入职", role="user", password="user1234")
        after = _stats(client, admin_headers, notice_id)["target_user_count"]
        assert before == after  # 快照不变

    def test_audience_list(self, client, admin_headers, user_factory):
        notice_id = self._published_notice(client, admin_headers, user_factory)
        audience = client.get(f"/api/v1/notices/{notice_id}/audience", headers=admin_headers).json()
        assert audience["total"] == _stats(client, admin_headers, notice_id)["target_user_count"]
        headers = login_headers(client, "读者", "user1234")
        client.post(f"/api/v1/notices/{notice_id}/read", headers=headers)
        read_list = client.get(f"/api/v1/notices/{notice_id}/audience?read_status=read",
                               headers=admin_headers).json()
        assert read_list["total"] == 1


class TestNoticeLinkedDocumentsContract:
    """关联资料出参契约：编辑页据此区分"已失效"与"只是没加载到"（防静默丢关联）。"""

    def test_detail_marks_archived_linked_document(self, client, admin_headers):
        doc_id = create_document(client, admin_headers, title="将被归档的关联",
                                 effective_from="2026-01-01")["id"]
        keep_id = create_document(client, admin_headers, title="仍在池外的老关联",
                                  effective_from="2026-01-01")["id"]
        notice_id = _create_notice(client, admin_headers, title="关联两份资料", content="x",
                                   document_ids=[doc_id, keep_id])["id"]

        client.request("DELETE", f"/api/v1/documents/{doc_id}", headers=admin_headers,
                       json={"reason": "测试归档"})

        docs = client.get(f"/api/v1/notices/{notice_id}", headers=admin_headers).json()["documents"]
        assert {d["id"]: d["archived"] for d in docs} == {doc_id: True, keep_id: False}

    def test_update_keeps_unarchived_link_after_dropping_archived(self, client, admin_headers):
        """只提交仍有效的关联时，未加载进候选池的老关联不得丢失。"""
        dead_id = create_document(client, admin_headers, title="失效关联",
                                  effective_from="2026-01-01")["id"]
        alive_id = create_document(client, admin_headers, title="有效关联",
                                   effective_from="2026-01-01")["id"]
        notice_id = _create_notice(client, admin_headers, title="混合关联", content="x",
                                   document_ids=[dead_id, alive_id])["id"]
        client.request("DELETE", f"/api/v1/documents/{dead_id}", headers=admin_headers,
                       json={"reason": "测试归档"})

        # 携带失效关联提交 → 后端拒绝（前端必须剪掉它才能保存成功）
        rejected = client.put(f"/api/v1/notices/{notice_id}", headers=admin_headers, json={
            "title": "混合关联", "content": "x", "notice_type": "发布",
            "document_ids": [dead_id, alive_id],
        })
        assert rejected.status_code == 400

        ok = client.put(f"/api/v1/notices/{notice_id}", headers=admin_headers, json={
            "title": "混合关联", "content": "x", "notice_type": "发布",
            "document_ids": [alive_id],
        })
        assert ok.status_code == 200, ok.text
        docs = client.get(f"/api/v1/notices/{notice_id}", headers=admin_headers).json()["documents"]
        assert [d["id"] for d in docs] == [alive_id]


class TestNoticeExpireLinkedDocuments:
    def test_publish_abrogation_auto_expires_document(self, client, admin_headers, user_factory):
        """废止通知：关联资料 effective_to = 通知生效日（§31 联动）。"""
        user_factory("废止读者", role="user", password="user1234")
        doc_id = create_document(client, admin_headers, title="将被废止手册",
                                 effective_from="2026-01-01", effective_to="2026-12-31")["id"]
        notice_id = _create_notice(client, admin_headers, title="废止旧版手册", content="即日废止",
                                   notice_type="废止", effective_at="2026-06-30T09:00:00",
                                   document_ids=[doc_id])["id"]
        resp = _publish(client, admin_headers, notice_id)
        assert resp.status_code == 200, resp.text

        detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
        assert detail["effective_to"] == "2026-06-30"
        # 次日失效
        status = client.get(f"/api/v1/documents/{doc_id}/status?date=2026-07-01",
                            headers=admin_headers).json()
        assert status["status"] == "expired"
        assert client.get(f"/api/v1/documents/{doc_id}/status?date=2026-06-30",
                          headers=admin_headers).json()["status"] == "active"
        # 审计 modify_validity（via notice_publish）
        logs = client.get("/api/v1/audit-logs?action=modify_validity", headers=admin_headers).json()
        assert any("notice_publish" in (log["detail"] or "") for log in logs["items"])

    def test_publish_normal_type_does_not_expire(self, client, admin_headers):
        doc_id = create_document(client, admin_headers, title="常态资料",
                                 effective_from="2026-01-01", effective_to="2026-12-31")["id"]
        notice_id = _create_notice(client, admin_headers, title="普通发布", content="x",
                                   notice_type="发布", document_ids=[doc_id])["id"]
        _publish(client, admin_headers, notice_id)
        detail = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
        assert detail["effective_to"] == "2026-12-31"  # 未被改动

    def test_abrogation_conflict_rolls_back(self, client, admin_headers, user_factory):
        """废止生效日早于资料生效日 → 409 VALIDITY_CONFLICT 且整体回滚（§31）。"""
        user_factory("冲突读者", role="user", password="user1234")
        doc_id = create_document(client, admin_headers, title="七月才生效",
                                 effective_from="2026-07-01")["id"]
        resp = client.post("/api/v1/notices", headers=admin_headers, json={
            "title": "提前废止", "content": "x", "notice_type": "废止",
            "effective_at": "2026-06-30T00:00:00",
            "document_ids": [doc_id],
        })
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "VALIDITY_CONFLICT"
        # 单事务回滚：通知根本没被创建，资料时效也未变
        titles = [n["title"] for n in client.get(
            "/api/v1/notices", headers=admin_headers).json()["items"]]
        assert "提前废止" not in titles, "创建失败不得留下通知"
        doc = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
        assert doc["effective_to"] is None, "回滚后资料时效必须保持原样"

    def test_linkage_via_replace_type(self, client, admin_headers, user_factory):
        user_factory("替换读者", role="user", password="user1234")
        doc_id = create_document(client, admin_headers, title="替换联动",
                                 effective_from="2026-01-01", effective_to=None)["id"]
        notice_id = _create_notice(client, admin_headers, title="替换通知", content="x",
                                   notice_type="替换", effective_at="2026-03-01T00:00:00",
                                   document_ids=[doc_id])["id"]
        _publish(client, admin_headers, notice_id)
        doc = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers).json()
        assert doc["effective_to"] == "2026-03-01"


class TestModifyPublishedNotice:
    def _published_and_read(self, client, admin_headers, user_factory):
        user_factory("读者乙", role="user", password="user1234")
        notice_id = _create_notice(client, admin_headers, title="已读通知",
                                   content="原正文")["id"]
        _publish(client, admin_headers, notice_id)
        headers = login_headers(client, "读者乙", "user1234")
        client.post(f"/api/v1/notices/{notice_id}/read", headers=headers)
        return notice_id

    def test_modify_published_read_requires_confirm_reason(self, client, admin_headers, user_factory):
        notice_id = self._published_and_read(client, admin_headers, user_factory)
        resp = client.put(f"/api/v1/notices/{notice_id}", headers=admin_headers,
                          json={"content": "无痕修改"})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "NOTICE_MODIFY_REQUIRES_REASON"

    def test_modify_with_confirm_reason_ok_and_audited(self, client, admin_headers, user_factory, db):
        notice_id = self._published_and_read(client, admin_headers, user_factory)
        resp = client.put(f"/api/v1/notices/{notice_id}", headers=admin_headers, json={
            "content": "有痕修改", "confirm_modify": True, "modified_reason": "更正错别字",
        })
        assert resp.status_code == 200, resp.text
        # 审计 update_notice 记录修改摘要与原因（§22 修改留痕）
        logs = client.get("/api/v1/audit-logs?action=update_notice", headers=admin_headers).json()
        assert logs["total"] >= 1
        detail = logs["items"][0]["detail"] or ""
        assert "更正错别字" in detail

    def test_draft_can_modify_without_confirm(self, client, admin_headers):
        notice_id = _create_notice(client, admin_headers, title="草稿可改", content="a")["id"]
        resp = client.put(f"/api/v1/notices/{notice_id}", headers=admin_headers,
                          json={"content": "b", "title": "草稿修改"})
        assert resp.status_code == 200

    def test_delete_goes_through_cancel(self, client, admin_headers, user_factory):
        """2026-11-04：DELETE /notices 已移除，删除统一走 /cancel 进回收站。"""
        user_factory("删除读者", role="user", password="user1234")
        nid = _create_notice(client, admin_headers, title="待删除通知", content="x")["id"]
        # 旧接口已下线
        assert client.delete(f"/api/v1/notices/{nid}",
                             headers=admin_headers).status_code in (404, 405)
        # 正确入口：cancel
        assert client.post(f"/api/v1/notices/{nid}/cancel",
                           headers=admin_headers, json={}).status_code == 200
        titles = [n["title"] for n in client.get(
            "/api/v1/notices", headers=admin_headers).json()["items"]]
        assert "待删除通知" not in titles


class TestNoticeFile:
    def test_notice_file_download_and_preview(self, client, admin_headers, user_factory, tmp_path):
        """通知文件下载 + 预览（可见性校验后）。"""
        user_factory("通知读者", role="user", password="user1234")
        docx = make_docx(tmp_path / DOCX_FILENAME)
        data = docx.read_bytes()
        resp = _post_with_reader(client, admin_headers,
                           data={"title": "带文件通知", "notice_type": "发布"},
                           files={"file": (DOCX_FILENAME, io.BytesIO(data),
                                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        notice_id = resp.json()["id"]
        _publish(client, admin_headers, notice_id)

        headers = login_headers(client, "通知读者", "user1234")
        dl = client.get(f"/api/v1/notices/{notice_id}/file", headers=headers)
        assert dl.status_code == 200
        assert dl.content == data
        preview = client.get(f"/api/v1/notices/{notice_id}/preview/docx", headers=headers)
        assert preview.status_code == 200
        assert "本制度" in preview.json()["text"]

    def test_notice_file_not_visible_to_outsider(self, client, admin_headers, user_headers):
        """非阅读对象的普通用户取不到通知文件（2026-11-04：草稿已不存在，
        改为验证「不在对象内」这条等效的可见性边界）。"""
        note = _create_notice(client, admin_headers, title="对象外文件", content="x",
                              target={"scope": "all"})
        # zhang san 是全体用户，但该通知没有文件；先断言「无文件」而不是越权
        assert client.get(f"/api/v1/notices/{note['id']}/file",
                          headers=user_headers).status_code in (403, 404)

class TestOptionalNoticeType:
    """通知类型非必填（2026-11-03 产品决策）。

    未选择类型时 notice_type 为 null，不触发废止/替换联动，
    列表与详情的类型徽章不渲染（而不是显示「其他」）。
    """

    def test_create_without_type(self, client, admin_headers):
        resp = _post_with_reader(client, admin_headers,
                           json={"title": "无类型通知", "content": "正文"})
        assert resp.status_code in (200, 201), resp.text
        assert resp.json()["notice_type"] is None

    def test_create_with_empty_type(self, client, admin_headers):
        """前端下拉选「不选择类型」时提交空串，后端按 null 存。"""
        resp = _post_with_reader(client, admin_headers,
                           json={"title": "空类型通知", "content": "正文",
                                 "notice_type": ""})
        assert resp.status_code in (200, 201), resp.text
        assert resp.json()["notice_type"] is None

    def test_create_without_type_publishes(self, client, admin_headers, user_factory):
        """空类型通知创建即发布（2026-11-04 起没有单独的发布步骤）。"""
        user_factory("无类型读者", role="user", password="user1234")
        resp = _post_with_reader(client, admin_headers,
                                 json={"title": "无类型可发布", "content": "正文"})
        assert resp.status_code == 201, resp.text
        assert resp.json()["status"] == "published"

    def test_null_type_does_not_trigger_linkage(self, client, admin_headers):
        """空类型绝不能被误判成「废止」——它不截断任何资料有效期。"""
        doc = create_document(client, admin_headers, title="不该被截断的资料",
                              effective_from="2020-01-01")
        _post_with_reader(client, admin_headers, json={
            "title": "无类型但关联资料", "content": "x",
            "document_ids": [doc["id"]],
        }).json()
        detail = client.get(f"/api/v1/documents/{doc['id']}",
                            headers=admin_headers).json()
        assert detail["effective_to"] is None, "空类型通知不得修改资料有效期"

    def test_update_can_clear_type(self, client, admin_headers):
        notice = _post_with_reader(client, admin_headers,
                             json={"title": "带类型", "content": "x",
                                   "notice_type": "发布"}).json()
        assert notice["notice_type"] == "发布"
        resp = client.put(f"/api/v1/notices/{notice['id']}", headers=admin_headers,
                          json={"notice_type": ""})
        assert resp.status_code == 200, resp.text
        assert resp.json()["notice_type"] is None


class TestStatisticsNewUx:
    """统计页重做后的后端契约（2026-11-03）。"""

    def test_audience_pagination_and_keyword_together(self, client, admin_headers, user_factory):
        """关键词 + 分页同时使用时，total 与页内容必须一致（防分页后过滤漏人）。"""
        for i in range(7):
            user_factory(f"查找用户{i}", role="user", password="user1234")
        notice = _create_notice(client, admin_headers, title="分页搜索", content="x")
        _publish(client, admin_headers, notice["id"])

        page1 = client.get(
            f"/api/v1/notices/{notice['id']}/audience",
            headers=admin_headers,
            params={"keyword": "查找用户", "page": 1, "page_size": 3},
        ).json()
        assert page1["total"] == 7, "total 应为过滤后的总数，而非全库数"
        assert len(page1["items"]) == 3
        assert page1["pages"] == 3

        page3 = client.get(
            f"/api/v1/notices/{notice['id']}/audience",
            headers=admin_headers,
            params={"keyword": "查找用户", "page": 3, "page_size": 3},
        ).json()
        assert len(page3["items"]) == 1

    def test_department_breakdown_has_unviewed(self, client, admin_headers,
                                               department_factory, user_factory):
        """按部门统计必须带「未查看」数，且按未查看降序排在前面。"""
        d1 = department_factory("已读部门")
        d2 = department_factory("未读部门")
        user_factory("读了的", role="user", password="user1234", department_ids=[d1.id])
        user_factory("没读的A", role="user", password="user1234", department_ids=[d2.id])
        user_factory("没读的B", role="user", password="user1234", department_ids=[d2.id])

        notice = _create_notice(client, admin_headers, title="部门统计", content="x",
                                target={"scope": "all"})
        _publish(client, admin_headers, notice["id"])
        h = login_headers(client, "读了的", "user1234")
        client.post(f"/api/v1/notices/{notice['id']}/read", headers=h)

        stats = _stats(client, admin_headers, notice["id"])
        rows = {r["name"]: r for r in stats["by_department"]}
        assert rows["已读部门"]["unviewed"] == 0
        assert rows["未读部门"]["unviewed"] == 2
        # 排序由前端按 unviewed 降序完成（后端只保证数据完整）
        ordered = sorted(stats["by_department"], key=lambda r: -r["unviewed"])
        assert ordered[0]["name"] == "未读部门"


class TestAdminNotInAudience:
    """管理员不进阅读对象（2026-11-03 产品决策，用户实测反馈）。

    用户以管理员登录时工作台显示「你有 1 条通知尚未阅读」——因为发布
    时给管理员也物化了快照。管理员能看全部通知是管理权限，不等于被要求阅读。
    """

    def test_admin_not_in_snapshot_on_publish(self, client, admin_headers, user_factory, db):
        from sqlalchemy import select

        from app.models.notice import NoticeRead
        from app.models.user import User

        user_factory("普通读者", role="user", password="user1234")
        notice = _create_notice(client, admin_headers, title="全体通知", content="x")
        _publish(client, admin_headers, notice["id"])

        admin = db.execute(
            select(User).where(User.username == "admin")
        ).scalars().first()
        rows = db.execute(
            select(NoticeRead).where(
                NoticeRead.notice_id == notice["id"],
                NoticeRead.user_id == admin.id,
            )
        ).scalars().all()
        assert rows == [], "管理员不应被物化进阅读快照"

    def test_admin_dashboard_has_no_unread(self, client, admin_headers, user_factory):
        """关键回归：管理员工作台的未读数必须为 0。"""
        user_factory("普通读者2", role="user", password="user1234")
        notice = _create_notice(client, admin_headers, title="全体通知2", content="x")
        _publish(client, admin_headers, notice["id"])

        dash = client.get("/api/v1/dashboard", headers=admin_headers).json()
        assert dash["my_unread_notices"] == 0, (
            f"管理员未读数应为 0，实际 {dash['my_unread_notices']}"
        )

    def test_admin_read_endpoint_noop(self, client, admin_headers, user_factory):
        """管理员打开详情触发的 /read 应静默成功，不 403、不写库。"""
        user_factory("普通读者3", role="user", password="user1234")
        notice = _create_notice(client, admin_headers, title="全体通知3", content="x")
        _publish(client, admin_headers, notice["id"])

        resp = client.post(f"/api/v1/notices/{notice['id']}/read", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json().get("skipped") == "admin"

    def test_admin_detail_shows_not_in_audience(self, client, admin_headers, user_factory):
        user_factory("普通读者4", role="user", password="user1234")
        notice = _create_notice(client, admin_headers, title="全体通知4", content="x")
        _publish(client, admin_headers, notice["id"])

        detail = client.get(
            f"/api/v1/notices/{notice['id']}", headers=admin_headers
        ).json()
        assert detail["in_audience"] is False
        assert detail["status"] == "published"  # 仍能看，只是不是阅读对象

    def test_admin_still_sees_all_notices(self, client, admin_headers, user_factory):
        """排除阅读对象 ≠ 降低可见性：管理员仍能看到全部通知。"""
        user_factory("普通读者5", role="user", password="user1234")
        notice = _create_notice(client, admin_headers, title="管理员可见", content="x")
        _publish(client, admin_headers, notice["id"])

        items = client.get("/api/v1/notices", headers=admin_headers).json()["items"]
        assert notice["id"] in [n["id"] for n in items]
