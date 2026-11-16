"""M42 操作日志：多条件筛选、翻页保留选择、单条/批量删除与审计对冲（项目规范 §25）。

§25 补充决策：删除本身可追溯——每次删除写一条汇总审计（audit_delete，含被删 id 清单
与操作人）；不提供「按筛选全删 / 清空全部」。本模块对该约束做正向验证。
"""
from __future__ import annotations


from harness import REPORT, SHOT_DIR, case, http


def _open(app):
    s = app.admin
    s.goto("/admin/audit-logs")
    s.page.wait_for_selector("#log-tbody tr", timeout=15000)
    s.page.wait_for_timeout(1500)
    return s


def _query(app, **kw) -> dict:
    from urllib.parse import urlencode

    params = {"page": kw.pop("page", 1), "page_size": kw.pop("page_size", 100)}
    for k, v in kw.items():
        if v not in (None, ""):
            params[k] = v
    # 中文关键词必须编码，否则服务端 400 且返回非 JSON，body 会退化成 str
    r = http("GET", "/api/v1/audit-logs?" + urlencode(params), app.admin.token)
    body = r["body"]
    return body if isinstance(body, dict) else {}


@case("L01", "日志列表：渲染 + 操作人服务端搜索 + 操作类型下拉已填充")
def l01(app):
    s = _open(app)
    total = _query(app).get("total") or 0
    rows = s.page.query_selector_all("#log-tbody tr")
    empty = "暂无日志记录" in s.page.inner_text("#log-tbody")
    REPORT.check(total == 0 or len(rows) > 0,
                 f"日志接口 {total} 条 / 页面 {len(rows)} 行（空态：{empty}）")
    REPORT.check(s.page.locator("#f-user").count() == 1,
                 "操作人筛选为服务端搜索输入框")
    REPORT.check("服务端搜索" in (s.page.get_attribute("#f-user", "placeholder") or ""),
                 "操作人输入框提示服务端搜索")
    actions = s.page.eval_on_selector_all("#f-action option", "els => els.map(e => e.value)")
    REPORT.check("登录" in s.page.inner_text("#f-action"), "操作类型下拉含中文标签「登录」")
    REPORT.check(len(actions) > 10, f"操作类型枚举 {len(actions)} 项")
    s.page.fill("#f-user", "admin")
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1600)
    REPORT.check(
        not ("暂无日志记录" in s.page.inner_text("#log-tbody") and total > 0),
        "操作人服务端搜索可用",
    )
    s.page.screenshot(path=str(SHOT_DIR / "L01_audit_logs.png"))

@case("L02", "筛选：按操作类型 / 对象类型 / 关键词过滤结果一致")
def l02(app):
    s = _open(app)
    # 用一条必然会存在的登录日志做基准
    base = _query(app, action="login")
    s.page.select_option("#f-action", "login")
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1800)
    page_rows = s.page.query_selector_all("#log-tbody tr")
    api_n = len(base.get("items") or [])
    if api_n:
        REPORT.check(len(page_rows) == min(api_n, 20),
                     f"操作类型=登录：页面 {len(page_rows)} 行 vs 接口 {api_n} 条")
        texts = " ".join(r.inner_text() for r in page_rows)
        REPORT.check("登录" in texts, "行内含「登录」操作标签")
    else:
        REPORT.note("无登录日志，跳过逐行内容校验")

    s.page.click("#btn-reset")
    s.page.wait_for_timeout(1500)

    # 关键词：按 API.md §9 口径，keyword 只匹配 object_label / detail（不匹配操作人）
    any_item = (_query(app, page_size=1).get("items") or [])
    kw = ""
    if any_item:
        kw = (any_item[0].get("object_label") or "").strip()
    if kw:
        s.page.fill("#f-keyword", kw)
        s.page.click("#btn-search")
        s.page.wait_for_timeout(1800)
        res = _query(app, keyword=kw)
        REPORT.check(len(res.get("items") or []) > 0,
                     f"关键词「{kw}」检索命中 {res.get('total')} 条")
        body = s.page.inner_text("#log-tbody")
        REPORT.check(kw in body or "暂无日志记录" in body, "页面结果与该关键词一致")
    # 明确记录：keyword 不覆盖「操作人」列，属契约内口径（见 API.md §9）
    miss = _query(app, keyword="admin_demo")
    REPORT.note(f"keyword 仅匹配 object_label/detail（操作人请用「操作人」下拉），"
                f"搜 admin_demo 命中 {(miss.get('total') or 0)} 条")
    s.page.click("#btn-reset")
    s.page.wait_for_timeout(1200)


@case("L03", "批量选择：全选本页 + 批量条计数准确 + 全选框状态自洽")
def l03(app):
    s = _open(app)
    rows = s.page.query_selector_all("#log-tbody tr")
    if not rows or "暂无日志记录" in s.page.inner_text("#log-tbody"):
        REPORT.note("无日志可选，跳过")
        return
    REPORT.check(s.page.is_hidden("#audit-bulk-bar"), "初始不显示批量条")
    n = len(s.page.query_selector_all("#log-tbody .audit-row-check"))

    # 用 click 而非 check：updateBulkBar() 里强制 checkAll.checked=false 会让 play
    # wright 的 check() 判定「点击未改变状态」而抛错——这正是要观测的缺陷
    s.page.click("#audit-check-all")
    s.page.wait_for_timeout(800)
    checked_rows = s.page.eval_on_selector_all(
        "#log-tbody .audit-row-check", "els => els.filter(e => e.checked).length")
    check_all_state = s.page.eval_on_selector("#audit-check-all", "e => e.checked")
    shown = s.page.inner_text("#audit-bulk-num").strip()
    REPORT.check(checked_rows == n, f"全选后所有行均勾选：{checked_rows}/{n}")
    REPORT.check(shown == str(n), f"批量条计数与行数一致：显示 {shown}")
    REPORT.check(s.page.is_visible("#audit-bulk-bar"), "批量条已显示")
    # 【缺陷观测】全选后被代码复位：行已全选但「全选」框自己变回未选中
    REPORT.check(check_all_state is True,
                 f"「全选本页」复选框在全选后应保持勾选（实际={check_all_state}）"
                 f"—— 若不勾选则为 UI 自洽缺陷：updateBulkBar() 无条件 checkAll.checked=false")

    s.page.click("#btn-audit-bulk-cancel")
    s.page.wait_for_timeout(700)
    REPORT.check(s.page.is_hidden("#audit-bulk-bar"), "取消后批量条隐藏")
    REPORT.check(s.page.inner_text("#audit-bulk-num").strip() == "0",
                 f"取消后计数复位（实际={s.page.inner_text('#audit-bulk-num').strip()}）")


@case("L04", "删除单条日志：确认弹窗 + 目标真消失 + 写入 audit_delete 对冲")
def l04(app):
    s = _open(app)
    rows = s.page.query_selector_all("#log-tbody tr")
    if not rows or "暂无日志记录" in s.page.inner_text("#log-tbody"):
        REPORT.note("无日志可删，跳过")
        return

    target = rows[0].query_selector(".audit-row-check")
    lid = int(target.get_attribute("data-id"))
    before_audit_ids = {i["id"] for i in (_query(app, action="audit_delete").get("items") or [])}

    rows[0].query_selector('button[data-action="del"]').click()
    s.page.wait_for_selector("#modal-root [data-confirm]", timeout=6000)
    msg = s.page.inner_text("#modal-root")
    REPORT.check("审计" in msg, f"弹窗说明会写入审计：{msg.replace(chr(10), ' ')[:50]}")
    s.page.click("#modal-root [data-confirm]")
    s.page.wait_for_timeout(2500)

    # 不能用 total 判定：删除动作本身又会新增 1 条 audit_delete，二者会互相抵消
    gone = http("GET", f"/api/v1/audit-logs/{lid}", app.admin.token)
    REPORT.check(gone["status"] == 404, f"被删日志 #{lid} 已不可访问：{gone['status']}")
    after_audit_ids = {i["id"] for i in (_query(app, action="audit_delete").get("items") or [])}
    added = after_audit_ids - before_audit_ids
    REPORT.check(bool(added), f"删除动作本身写入新审计条目：{sorted(added)[:3]}")


@case("L05", "批量删除多条：所选全部消失 + 一次合并留痕")
def l05(app):
    s = _open(app)
    boxes = s.page.query_selector_all("#log-tbody .audit-row-check")
    if len(boxes) < 2 or "暂无日志记录" in s.page.inner_text("#log-tbody"):
        REPORT.note("日志不足两条，跳过批量删除")
        return
    # 取靠后的旧条目：避开刚写入的 audit_delete（否则会把审计证据一并删掉）
    picked = boxes[-3:] if len(boxes) >= 3 else boxes[-2:]
    ids = [int(b.get_attribute("data-id")) for b in picked]
    for b in picked:
        b.click()
    s.page.wait_for_timeout(700)
    REPORT.check(s.page.inner_text("#audit-bulk-num").strip() == str(len(ids)),
                 f"已勾选 {len(ids)} 条，批量条显示 {s.page.inner_text('#audit-bulk-num').strip()}")

    before_audit_ids = {i["id"] for i in (_query(app, action="audit_delete").get("items") or [])}
    s.page.click("#btn-audit-batch-delete")
    s.page.wait_for_selector("#modal-root [data-confirm]", timeout=6000)
    s.page.click("#modal-root [data-confirm]")
    s.page.wait_for_timeout(2800)

    survivors = [i for i in ids
                 if http("GET", f"/api/v1/audit-logs/{i}", app.admin.token)["status"] != 404]
    REPORT.check(not survivors, f"所选 {len(ids)} 条全部消失（残留 {survivors}）")
    after_audit_ids = {i["id"] for i in (_query(app, action="audit_delete").get("items") or [])}
    added = after_audit_ids - before_audit_ids
    REPORT.check(len(added) == 1,
                 f"整批删除合并为 1 条汇总审计（实际新增 {len(added)} 条）")
    if added:
        new_id = sorted(added)[0]
        detail = str((http("GET", f"/api/v1/audit-logs/{new_id}",
                           app.admin.token)["body"] or {}).get("detail") or "")
        REPORT.check(str(ids[0]) in detail or "deleted_ids" in detail,
                     f"汇总审计含被删 id 清单：{detail[:70]}")
    REPORT.check(s.page.is_hidden("#audit-bulk-bar"), "删除后批量条复位")
    s.page.screenshot(path=str(SHOT_DIR / "L05_audit_deleted.png"))


@case("L06", "约束：不提供「按筛选全删 / 清空全部」入口（§25 补充决策）")
def l06(app):
    s = _open(app)
    html = s.page.inner_html("body")
    for kw in ("清空全部", "清空日志", "按筛选删除", "全部删除"):
        REPORT.check(kw not in html, f"页面不存在「{kw}」入口")
    # 也验证 API 侧未提供全删端点
    r = http("DELETE", "/api/v1/audit-logs", app.admin.token, {"confirm": True})
    REPORT.check(r["status"] in (404, 405, 401, 403),
                 f"DELETE /api/v1/audit-logs 未提供全删：{r['status']}")


@case("L07", "权限：普通用户不得查看/删除日志")
def l07(app):
    app.allow_noise()
    items = (_query(app, page_size=1).get("items") or [])
    lid = items[0].get("id") if items else 1
    for method, path, payload in (
        ("GET", "/api/v1/audit-logs", None),
        ("POST", "/api/v1/audit-logs/batch-delete", {"ids": [lid]}),
    ):
        r = http(method, path, app.user.token, payload)
        REPORT.check(r["status"] in (401, 403), f"{method} {path} → {r['status']}")
    u = app.user
    u.goto("/admin/audit-logs", ensure_session=True)
    u.page.wait_for_timeout(1200)
    REPORT.check("/admin/audit-logs" not in u.page.url, f"普通用户被挡回：{u.page.url}")
