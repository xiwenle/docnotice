"""M03 工作台：通知条幅、最近资料/最新通知、顶条全局搜索。

2026-11-03：管理待办五卡已移除（产品决策），相关断言改为「不得出现」。
"""
from __future__ import annotations

from harness import REPORT, SHOT_DIR, case, http


def _open(app):
    s = app.admin
    s.goto("/")
    s.page.wait_for_selector("#recent-documents", timeout=15000)
    s.page.wait_for_timeout(1500)
    return s


@case("W01", "工作台：聚合数据渲染（最近资料 / 最新通知 / 无管理待办）")
def w01(app):
    s = _open(app)
    dash = http("GET", "/api/v1/dashboard", app.admin.token)["body"] or {}
    REPORT.check(isinstance(dash.get("my_unread_notices"), int),
                 f"聚合接口返回未读计数：{dash.get('my_unread_notices')}")
    REPORT.check("my_unconfirmed_notices" not in dash,
                 "通知确认正式下线后聚合响应不再含待确认字段")

    docs = s.page.query_selector_all("#recent-documents a, #recent-documents .row-title")
    REPORT.check(len(docs) > 0, f"最近资料区块渲染 {len(docs)} 条")
    notices = s.page.query_selector_all("#recent-notices a, #recent-notices .row-title")
    REPORT.check(len(notices) > 0, f"最新通知区块渲染 {notices and len(notices)} 条")

    has_todo = s.page.is_visible("#todo-banner")
    n_todo = dash.get("my_unread_notices") or 0
    REPORT.check(has_todo == (n_todo > 0),
                 f"通知条幅可见性 = 有未读（{n_todo} 条 → 可见={has_todo}）")
    if has_todo:
        txt = s.page.inner_text("#todo-text")
        REPORT.check("通知" in txt, f"条幅文案含未读数量：{txt[:50]}")
        href = s.page.get_attribute("#todo-link", "href")
        REPORT.check(href == "/notices?unread_only=1",
                     f"条幅按钮直达未读筛选：{href}")

    REPORT.check(not s.page.is_visible("#admin-todo"),
                 "管理待办卡已移除（2026-11-03 产品决策）")
    for field in ("uncategorized_count", "untagged_count",
                  "expiring_30d_count", "draft_notice_count"):
        REPORT.check(field not in dash, f"聚合响应不再含 {field}")
    s.page.screenshot(path=str(SHOT_DIR / "W01_dashboard.png"))


@case("W02", "普通用户工作台：不出现管理字段")
def w02(app):
    s = app.user
    s.goto("/")
    s.page.wait_for_selector("#recent-documents", timeout=15000)
    s.page.wait_for_timeout(1500)
    REPORT.check(not s.page.is_visible("#admin-todo"), "普通用户同样看不到管理待办卡")
    dash = http("GET", "/api/v1/dashboard", app.user.token)["body"] or {}
    REPORT.check("uncategorized_count" not in dash,
                 "普通用户的聚合响应不含管理字段（后端按角色裁剪）")
    docs = s.page.query_selector_all("#recent-documents a, #recent-documents .row-title")
    REPORT.check(len(docs) > 0, f"普通用户可见最近资料 {len(docs)} 条")
    s.page.screenshot(path=str(SHOT_DIR / "W02_dashboard_user.png"))


@case("W03", "全局搜索：工作台搜索卡回车跳转资料页并带上关键词")
def w03(app):
    s = _open(app)
    kw = "手册"
    s.page.fill("#gs-input", kw)
    s.page.press("#gs-input", "Enter")
    s.page.wait_for_load_state("networkidle")
    s.page.wait_for_timeout(1500)
    REPORT.check("/documents" in s.page.url, f"跳转到资料列表：{s.page.url}")
    from urllib.parse import unquote, quote

    REPORT.check(f"keyword={quote(kw)}" in s.page.url or kw in unquote(s.page.url),
                 f"关键词已带入：{unquote(s.page.url)}")
    val = s.page.input_value("#f-keyword")
    REPORT.check(val == kw, f"搜索框回填关键词：{val}")


@case("W04", "搜索入口：列表页用页内搜索，其余页面用顶条搜索")
def w04(app):
    s = app.admin
    # 无页内搜索的页面：顶条可用
    s.goto("/admin/backups")
    s.page.wait_for_selector("#backup-tbody tr", timeout=15000)
    s.page.wait_for_timeout(800)
    focused = s.page.evaluate(
        "() => {document.body.focus(); document.dispatchEvent("
        "new KeyboardEvent('keydown', {key:'/', bubbles:true}));"
        " return document.activeElement && document.activeElement.id;}")
    REPORT.check(focused == "cb-q", f"按 / 聚焦顶条搜索框（activeElement={focused}）")
    s.page.fill("#cb-q", "制度")
    s.page.press("#cb-q", "Enter")
    s.page.wait_for_load_state("networkidle")
    s.page.wait_for_timeout(1200)
    REPORT.check("/documents" in s.page.url, f"顶条搜索跳转到资料页：{s.page.url}")
    REPORT.check(s.page.input_value("#f-keyword") == "制度",
                 "关键词已带入资料列表")

    # 有页内搜索的列表页：顶条隐藏，/ 聚焦页内搜索
    s.goto("/admin/audit-logs")
    s.page.wait_for_selector("#log-tbody tr", timeout=15000)
    s.page.wait_for_timeout(800)
    REPORT.check(s.page.is_hidden("#cb-search"), "列表页隐藏顶条搜索，避免两个入口")
    REPORT.check(s.page.is_visible("#cb-global-link")
                 and (s.page.get_attribute("#cb-global-link", "href") or "").endswith("/documents"),
                 "列表页保留「搜索资料」紧凑链接作为全局入口")
    local_focus = s.page.evaluate(
        "() => {document.body.focus(); document.dispatchEvent("
        "new KeyboardEvent('keydown', {key:'/', bubbles:true}));"
        " return document.activeElement && document.activeElement.id;}")
    REPORT.check(local_focus == "f-keyword", f"按 / 聚焦页内搜索（activeElement={local_focus}）")

    s.goto("/admin/backups")
    s.page.wait_for_selector("#backup-tbody tr", timeout=15000)
    s.page.wait_for_timeout(500)
    REPORT.check(s.page.is_hidden("#cb-global-link"),
                 "无页内搜索的页面不显示紧凑搜索链接")
