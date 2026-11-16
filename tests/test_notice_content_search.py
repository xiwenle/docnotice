"""通知正文搜索回归测试（2026-11-12）。

用户确认：通知列表 keyword 从「只搜标题」扩成「标题或纯文字正文」；
关联资料候选仍复用资料列表既有 title/summary 搜索，不解析文件正文。
"""
from __future__ import annotations

import io

from files.generators import PDF_FILENAME, make_pdf
from helpers import create_document, login_headers

from tests.test_notices import _create_notice


def _create_notice_with_file(client, headers, *, title, tmp_path):
    pdf = make_pdf(tmp_path / PDF_FILENAME)
    resp = client.post(
        "/api/v1/notices",
        headers=headers,
        data={"title": title, "notice_type": "发布"},
        files={"file": (PDF_FILENAME, io.BytesIO(pdf.read_bytes()), "application/pdf")},
    )
    # 全体对象时若没有普通用户则补一个，保持与既有通知测试 helper 一致。
    if resp.status_code == 400 and "阅读对象范围为空" in resp.text:
        client.post("/api/v1/users", headers=headers, json={
            "username": "通知文件读者", "password": "user1234",
            "display_name": "通知文件读者", "role": "user",
        })
        resp = client.post(
            "/api/v1/notices",
            headers=headers,
            data={"title": title, "notice_type": "发布"},
            files={"file": (PDF_FILENAME, io.BytesIO(pdf.read_bytes()), "application/pdf")},
        )
    assert resp.status_code == 201, f"创建纯文件通知失败 {resp.status_code}: {resp.text}"
    return resp.json()


class TestNoticeContentKeyword:
    def test_admin_and_user_can_search_notice_title_or_content(self, client, admin_headers, user_factory):
        target = user_factory("通知目标", role="user", password="user1234")
        other = user_factory("通知范围外", role="user", password="user1234")
        _create_notice(
            client, admin_headers,
            title="考勤制度调整", content="请各部门在本周五前完成排班确认",
        )
        private = _create_notice(
            client, admin_headers,
            title="研发专项", content="仅限研发成员查看的迭代说明",
            target={"scope": "user", "user_ids": [target.id]},
        )
        _create_notice(
            client, admin_headers,
            title="保密通知", content="范围外关键词：梧桐计划",
            target={"scope": "user", "user_ids": [other.id]},
        )

        admin_by_title = client.get(
            "/api/v1/notices", headers=admin_headers, params={"keyword": "考勤制度"}
        ).json()["items"]
        assert [n["title"] for n in admin_by_title] == ["考勤制度调整"]

        admin_by_content = client.get(
            "/api/v1/notices", headers=admin_headers, params={"keyword": "排班确认"}
        ).json()["items"]
        assert [n["title"] for n in admin_by_content] == ["考勤制度调整"]

        user_headers = login_headers(client, target.username, "user1234")
        user_by_content = client.get(
            "/api/v1/notices", headers=user_headers, params={"keyword": "迭代说明"}
        ).json()["items"]
        assert [n["id"] for n in user_by_content] == [private["id"]]

        hidden_by_content = client.get(
            "/api/v1/notices", headers=user_headers, params={"keyword": "梧桐计划"}
        ).json()["items"]
        assert hidden_by_content == []

        # 正文命中与类型/日期/分页组合仍按 SQL 条件执行，不退化成内存过滤。
        combined = client.get(
            "/api/v1/notices", headers=admin_headers,
            params={"keyword": "排班确认", "type": "发布", "page": 1, "page_size": 1},
        ).json()
        assert combined["total"] == 1
        assert [n["title"] for n in combined["items"]] == ["考勤制度调整"]

    def test_pure_file_notice_content_search_does_not_invent_file_text(self, client, admin_headers, tmp_path):
        notice = _create_notice_with_file(
            client, admin_headers, title="纯文件通知标题", tmp_path=tmp_path
        )
        assert notice["has_file"] is True
        assert notice["content"] in (None, "")

        by_title = client.get(
            "/api/v1/notices", headers=admin_headers, params={"keyword": "纯文件通知标题"}
        ).json()["items"]
        assert [n["id"] for n in by_title] == [notice["id"]]

        by_absent_text = client.get(
            "/api/v1/notices", headers=admin_headers, params={"keyword": "Company Internal Document Test"}
        ).json()["items"]
        assert by_absent_text == []


class TestNoticeLinkedDocumentSearch:
    def test_document_candidate_api_matches_title_or_summary_for_admin(self, client, admin_headers):
        by_title = create_document(
            client, admin_headers, title="考勤资料", summary="普通说明"
        )
        by_summary = create_document(
            client, admin_headers, title="普通标题", summary="关联资料摘要命中：紫藤花"
        )

        title_hits = client.get(
            "/api/v1/documents", headers=admin_headers, params={"keyword": "考勤资料"}
        ).json()["items"]
        summary_hits = client.get(
            "/api/v1/documents", headers=admin_headers, params={"keyword": "紫藤花"}
        ).json()["items"]

        assert by_title["id"] in [d["id"] for d in title_hits]
        assert by_summary["id"] in [d["id"] for d in summary_hits]
        assert by_title["id"] not in [d["id"] for d in summary_hits]

    def test_search_ui_copy_is_declared_in_templates(self):
        root = __import__("pathlib").Path(__file__).resolve().parents[1]
        notices = (root / "app" / "web" / "templates" / "notices.html").read_text(encoding="utf-8")
        form = (root / "app" / "web" / "templates" / "admin" / "notice_form.html").read_text(encoding="utf-8")
        js = (root / "app" / "web" / "static" / "js" / "admin" / "notice_form.js").read_text(encoding="utf-8")
        assert "搜索通知：标题、正文…" in notices
        assert "搜索资料：标题、说明、摘要…" in form
        # 候选列表只按服务端结果排序置顶；不得再按 title 做本地二次过滤。
        assert "!kw || (d.title || \"\").toLowerCase().includes(kw)" not in js
        assert "function renderDocList(kw)" in js
