"""M73 通知正文 / 关联资料搜索：真浏览器双线专项。

覆盖用户确认的两点：
1. 通知列表搜索框可按通知标题或纯文字正文检索；
2. 新建通知页「关联资料」候选与资料列表一致，按标题或资料说明/摘要检索。
不解析通知文件或资料文件里的文字，不引入全文索引。
"""
from __future__ import annotations

import time
import urllib.parse

from harness import REPORT, SHOT_DIR, case, http


def _body(resp) -> dict:
    b = resp.get("body")
    return b if isinstance(b, dict) else {}


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token} if token else {}


def _api(method: str, path: str, token: str = "", payload=None):
    return http(method, path, token, payload)


def _create_notice(token: str, title: str, content: str, *, user_ids=None):
    target = {"scope": "all", "department_ids": [], "user_ids": []}
    if user_ids is not None:
        target = {"scope": "user", "department_ids": [], "user_ids": user_ids}
    return _api("POST", "/api/v1/notices", token, {
        "title": title,
        "content": content,
        "notice_type": "发布",
        "target": target,
    })


def _user_id(app, username: str):
    r = _api("GET", "/api/v1/users?keyword=" + urllib.parse.quote(username) + "&page_size=100", app.admin.token)
    for item in (_body(r).get("items") or []):
        if item.get("username") == username:
            return item.get("id")
    return None


def _wait_candidates(s, needle: str, timeout_ms: int = 6000):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        text = s.page.inner_text("#doc-list") or ""
        if needle in text:
            return text
        s.page.wait_for_timeout(200)
    return s.page.inner_text("#doc-list") or ""


@case("Q01", "通知列表：管理员可用标题或正文关键词搜索")
def q01(app):
    marker = f"Q01正文标记{int(time.time())}"
    title = f"E2E通知正文搜索-{marker}"
    created = _create_notice(app.admin.token, title, "请在本周五前完成" + marker)
    REPORT.check(created.get("status") == 201, f"创建正文通知：{created.get('status')}")
    nid = (_body(created) or {}).get("id")
    REPORT.check(bool(nid), f"取得通知 id：{nid}")

    s = app.admin
    s.goto("/notices")
    s.page.wait_for_selector("#f-keyword", timeout=12000)
    placeholder = s.page.get_attribute("#f-keyword", "placeholder") or ""
    REPORT.check("标题" in placeholder and "正文" in placeholder,
                 f"通知搜索框文案提示标题+正文：{placeholder}")

    s.page.fill("#f-keyword", marker)
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1200)
    body = s.page.inner_text("#notice-list") or ""
    REPORT.check(title in body, f"正文关键词命中标题：{title in body}")
    REPORT.check(f"keyword={urllib.parse.quote(marker)}" in s.page.url or marker in urllib.parse.unquote(s.page.url),
                 "URL 保留关键词筛选状态")

    s.page.fill("#f-keyword", "本周五前完成" + marker)
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1200)
    body2 = s.page.inner_text("#notice-list") or ""
    REPORT.check(title in body2, f"正文内容命中通知：{title in body2}")
    s.page.screenshot(path=str(SHOT_DIR / "Q01_admin_notice_content_search.png"))


@case("Q02", "通知列表：普通用户正文命中且不泄露范围外通知")
def q02(app):
    marker = f"Q02正文标记{int(time.time())}"
    uid = _user_id(app, app.user.user.get("username") or "user_rnd_1")
    REPORT.check(bool(uid), f"取得普通用户 id：{uid}")
    visible = _create_notice(app.admin.token, f"E2E用户可见-{marker}", "自己的" + marker, user_ids=[uid] if uid else None)
    REPORT.check(visible.get("status") == 201, f"创建指定用户通知：{visible.get('status')}")
    hidden_uid = _user_id(app, "user_fin_1")
    REPORT.check(bool(hidden_uid), f"取得范围外用户 id：{hidden_uid}")
    hidden = _create_notice(
        app.admin.token, f"E2E用户范围外-{marker}", "范围外" + marker,
        user_ids=[hidden_uid] if hidden_uid else None,
    )
    REPORT.check(hidden.get("status") == 201, f"创建范围外通知：{hidden.get('status')}")

    s = app.user
    s.goto("/notices")
    s.page.wait_for_selector("#f-keyword", timeout=12000)
    s.page.fill("#f-keyword", marker)
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1200)
    body = s.page.inner_text("#notice-list") or ""
    REPORT.check(f"E2E用户可见-{marker}" in body, "普通用户正文关键词命中自己可见通知")
    REPORT.check(f"E2E用户范围外-{marker}" not in body, "普通用户正文关键词不会泄露范围外通知")
    s.page.screenshot(path=str(SHOT_DIR / "Q02_user_notice_content_scope.png"))


@case("Q03", "新建通知：关联资料候选按标题或资料说明/摘要搜索")
def q03(app):
    marker = f"Q03仅说明标记{int(time.time())}"
    title_doc = f"E2E关联资料标题-{int(time.time())}"
    summary_doc = "E2E关联资料普通标题-标题无关键词"
    d1 = _api("POST", "/api/v1/documents", app.admin.token, {
        "title": title_doc, "summary": "普通资料说明", "effective_from": "2026-01-01", "audience_scope": "all",
    })
    d2 = _api("POST", "/api/v1/documents", app.admin.token, {
        "title": summary_doc, "summary": "关联候选摘要命中：" + marker,
        "effective_from": "2026-01-01", "audience_scope": "all",
    })
    REPORT.check(d1.get("status") == 201 and d2.get("status") == 201,
                 f"创建两份测试资料：{d1.get('status')}/{d2.get('status')}")

    s = app.admin
    s.goto("/admin/notices/new")
    s.page.wait_for_selector("#doc-search", timeout=12000)
    placeholder = s.page.get_attribute("#doc-search", "placeholder") or ""
    REPORT.check("标题" in placeholder and "说明" in placeholder and "摘要" in placeholder,
                 f"关联资料搜索框文案与资料列表一致：{placeholder}")

    s.page.fill("#doc-search", title_doc)
    s.page.wait_for_timeout(1000)
    by_title = s.page.inner_text("#doc-list") or ""
    REPORT.check(title_doc in by_title, "关联资料可按标题命中")

    s.page.fill("#doc-search", marker)
    s.page.wait_for_timeout(1000)
    by_summary = _wait_candidates(s, summary_doc)
    REPORT.check(summary_doc in by_summary, "关联资料可按说明/摘要关键词命中（标题不含该词）")
    REPORT.check(title_doc not in by_summary, "标题不含该词且说明不含该词的资料不混入结果")

    boxes = s.page.query_selector_all("#doc-list input[type=checkbox]")
    REPORT.check(len(boxes) == 1, f"说明命中时只展示服务端筛出的 1 条候选：{len(boxes)}")
    if boxes:
        boxes[0].check()
    s.page.fill("#doc-search", "")
    s.page.wait_for_timeout(1000)
    checked = s.page.query_selector_all("#doc-list input[type=checkbox]:checked")
    REPORT.check(len(checked) >= 1, "清空筛选后已勾选资料仍保留")
    s.page.screenshot(path=str(SHOT_DIR / "Q03_notice_form_linked_document_search.png"))


@case("Q04", "静态契约：不解析文件正文、不恢复确认功能")
def q04(app):
    s = app.admin
    s.goto("/notices")
    s.page.wait_for_selector("#f-keyword", timeout=12000)
    text = s.page.inner_text("body") or ""
    for word in ("确认阅读", "确认率", "待确认"):
        REPORT.check(word not in text, f"通知页无「{word}」残留")
    js = s.page.evaluate("""async () => {
      const r = await fetch('/static/js/admin/notice_form.js?v=20261111e');
      return r.status === 200 ? await r.text() : '';
    }""") or ""
    REPORT.check("!kw || (d.title || \"\").toLowerCase().includes(kw)" not in js,
                 "关联资料候选不再按 title 做本地二次过滤")
