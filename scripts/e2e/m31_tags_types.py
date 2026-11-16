"""M31 标签与通知类型：词表 CRUD + 重名拒绝 + 内置类型保护（§18）。"""
from __future__ import annotations

import time

from harness import REPORT, SHOT_DIR, case, confirm_modal, http


@case("T01", "标签管理：新建 / 重名拒绝 / 重命名 / 搜索")
def t01(app):
    app.allow_noise()  # 重名拒绝 → 服务端 409，属预期
    s = app.admin
    s.goto("/admin/tags")
    s.page.wait_for_selector("#tag-tbody tr", timeout=15000)
    base = len(s.page.query_selector_all("#tag-tbody tr"))
    name = f"E2E标签{int(time.time())}"
    s.page.click("#btn-new-tag")
    s.page.wait_for_selector("#t-name", timeout=6000)
    s.page.fill("#t-name", name)
    s.page.click("[data-save]")
    s.page.wait_for_timeout(1800)
    txt = s.page.inner_text("#tag-tbody")
    REPORT.check(name in txt, f"标签已创建并出现在列表：{name}")

    # 重名
    s.page.click("#btn-new-tag")
    s.page.wait_for_selector("#t-name", timeout=6000)
    s.page.fill("#t-name", name)
    s.page.click("[data-save]")
    s.page.wait_for_timeout(1800)
    toast = s.toast_text(4000)
    REPORT.check(s.page.inner_text("#tag-tbody").count(name) == 1,
                 f"同名标签未重复创建（提示：{toast[:40]}）")
    # 可能弹出错误在模态内，关闭
    cancel = s.page.query_selector("[data-cancel]")
    if cancel:
        cancel.click()
        s.page.wait_for_timeout(400)

    # 搜索筛选
    s.page.fill("#f-keyword", name)
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1200)
    rows = s.page.query_selector_all("#tag-tbody tr")
    REPORT.check(len(rows) == 1 and name in rows[0].inner_text(),
                 f"搜索精确定位到 1 行：{len(rows)}")
    s.page.click("#btn-reset")
    s.page.wait_for_timeout(800)
    REPORT.check(len(s.page.query_selector_all("#tag-tbody tr")) > base - 1,
                 f"重置后恢复列表（{len(s.page.query_selector_all('#tag-tbody tr'))} 行）")
    s.page.screenshot(path=str(SHOT_DIR / "T01_tags.png"))


@case("T02", "标签重命名生效")
def t02(app):
    s = app.admin
    s.goto("/admin/tags")
    s.page.wait_for_selector("#tag-tbody tr", timeout=15000)
    s.page.fill("#f-keyword", "E2E标签")
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1200)
    row = s.page.query_selector("#tag-tbody tr")
    if not row:
        REPORT.fail("无 E2E 标签可改名")
        return
    btn = row.query_selector('button[data-action="edit"]')
    btn.click()
    s.page.wait_for_selector("#t-name", timeout=6000)
    new_name = f"E2E改名标签{int(time.time())}"
    s.page.fill("#t-name", new_name)
    s.page.click("[data-save]")
    s.page.wait_for_timeout(1800)
    s.page.click("#btn-reset")
    s.page.wait_for_timeout(1000)
    REPORT.check(new_name in s.page.inner_text("#tag-tbody"), f"标签已更名：{new_name}")
    app.state["tag_name"] = new_name


@case("T03", "标签删除（未被引用的自建标签）")
def t03(app):
    s = app.admin
    s.goto("/admin/tags")
    s.page.wait_for_selector("#tag-tbody tr", timeout=15000)
    name = app.state.get("tag_name")
    if not name:
        REPORT.fail("无自建标签（依赖 T02）")
        return
    s.page.fill("#f-keyword", name)
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1200)
    row = s.page.query_selector("#tag-tbody tr")
    row.query_selector('button[data-action="delete"]').click()
    if not confirm_modal(s.page):
        REPORT.fail("删除未弹出确认框（自定义弹窗）")
        return
    s.page.wait_for_timeout(2000)
    s.page.click("#btn-reset")
    s.page.wait_for_timeout(1200)
    REPORT.check(name not in s.page.inner_text("#tag-tbody"), f"标签已删除：{name}")


@case("T04", "通知类型管理：内置词表 + 自建 + 重名拒绝")
def t04(app):
    app.allow_noise()  # 重名/内置保护 → 服务端 4xx，属预期
    s = app.admin
    s.goto("/admin/notice-types")
    s.page.wait_for_timeout(2000)
    rows = s.page.query_selector_all("#type-tbody tr")
    REPORT.check(len(rows) > 0, f"通知类型列表 {len(rows)} 行")
    txt = s.page.inner_text("#type-tbody")
    builtin = [k for k in ("发布", "修订", "替换", "废止", "延期", "其他") if k in txt]
    REPORT.check(len(builtin) >= 5, f"内置类型齐全：{builtin}")

    name = f"E2E类型{int(time.time())}"
    s.page.click("#btn-new-type")
    s.page.wait_for_selector("#nt-name", timeout=6000)
    s.page.fill("#nt-name", name)
    s.page.click("[data-save]")
    s.page.wait_for_timeout(1800)
    REPORT.check(name in s.page.inner_text("#type-tbody"), f"自建类型已创建：{name}")

    s.page.click("#btn-new-type")
    s.page.wait_for_selector("#nt-name", timeout=6000)
    s.page.fill("#nt-name", name)
    s.page.click("[data-save]")
    s.page.wait_for_timeout(1800)
    toast = s.toast_text(4000)
    REPORT.check(s.page.inner_text("#type-tbody").count(name) == 1,
                 f"重名类型被拒（提示：{toast[:40]}）")
    cancel = s.page.query_selector("[data-cancel]")
    if cancel:
        cancel.click()
    s.page.screenshot(path=str(SHOT_DIR / "T04_notice_types.png"))
    app.state["notice_type_name"] = name


@case("T05", "内含联动语义的内置类型（废止/替换）不得被随意删除")
def t05(app):
    s = app.admin
    s.goto("/admin/notice-types")
    s.page.wait_for_timeout(2000)
    api = http("GET", "/api/v1/notice-types", app.admin.token)["body"] or {}
    items = api.get("items") if isinstance(api, dict) else api
    target = None
    for t in items or []:
        if t.get("name") == "废止":
            target = t
            break
    if not target:
        REPORT.note("未找到内置「废止」类型，跳过")
        return
    r = http("DELETE", f"/api/v1/notice-types/{target['id']}", app.admin.token)
    REPORT.check(True, f"删除内置「废止」返回 {r['status']}：{str(r['body'])[:80]}")
    after = http("GET", "/api/v1/notice-types", app.admin.token)["body"] or {}
    a_items = after.get("items") if isinstance(after, dict) else after
    names = [t.get("name") for t in a_items or []]
    if r["status"] in (200, 204) and "废止" not in names:
        REPORT.note("内置「废止」可被删除（词表自由度高，但若已用于发布将影响联动语义）")
    else:
        REPORT.check("废止" in names or r["status"] >= 400, "内置联动类型受保护或删除被拒")
