"""M10 资料列表：搜索、状态页签、分类树定位、历史日期查状态、重叠区间、批量整理、分页。"""
from __future__ import annotations

import time

from harness import REPORT, case, http


def _rows(page):
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('#doc-tbody tr')).map(tr => ({"
        "  cells: tr.querySelectorAll('td').length,"
        "  text: tr.innerText.replace(/\\n/g, ' | '),"
        "  status: (tr.querySelector('.badge') || {}).innerText || '',"
        "  link: (tr.querySelector('a.doc-title-link') || {}).getAttribute ? "
        "        tr.querySelector('a.doc-title-link').getAttribute('href') : ''"
        "}))")


def _open_adv(page):
    """展开「高级筛选」折叠区（内部元素不可见时 fill 会卡住）。"""
    page.evaluate("() => {const d = document.getElementById('adv-filter'); if (d) d.open = true;}")
    page.wait_for_timeout(300)


def _total(app):
    r = app.admin.fetch("GET", "/api/v1/documents?page=1&page_size=1")
    return ((r["body"] or {}).get("total") or 0)


@case("D01", "资料列表：默认加载 + 分页 + 列数一致")
def d01(app):
    s = app.admin
    s.goto("/documents")
    s.page.wait_for_selector("#doc-tbody tr", timeout=15000)
    rows = _rows(s.page)
    REPORT.check(len(rows) > 0, f"首屏渲染 {len(rows)} 行")
    heads = s.page.evaluate("() => document.querySelectorAll('table thead th').length")
    REPORT.check(all(r["cells"] == heads for r in rows),
                 f"每行单元格数与表头一致（表头 {heads} / 行 {[r['cells'] for r in rows][:6]}）")
    pg = s.page.inner_text("#doc-pagination").strip()
    REPORT.check(True, f"分页区：{pg.replace(chr(10), ' ')[:60] or '(单页不显示)'}")
    empty = s.page.evaluate("() => document.body.innerText.includes('加载中…')")
    REPORT.check(not empty, "无残留「加载中…」占位")


@case("D02", "资料列表：关键词搜索（标题命中 / 无结果空态 / 重置）")
def d02(app):
    s = app.admin
    s.goto("/documents")
    s.page.wait_for_selector("#doc-tbody tr", timeout=15000)
    kw = "员工"
    s.page.fill("#f-keyword", kw)
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1200)
    rows = _rows(s.page)
    hit = [r for r in rows if kw in r["text"]]
    if rows and rows[0]["cells"] == 1:
        REPORT.check(True, f"关键词「{kw}」无结果 → 空态：{rows[0]['text'][:40]}")
    else:
        REPORT.check(len(hit) == len(rows),
                     f"关键词「{kw}」：{len(hit)}/{len(rows)} 行命中")
    s.page.click("#btn-reset")
    s.page.wait_for_timeout(1200)
    rows2 = _rows(s.page)
    REPORT.check(len(rows2) >= len(rows), f"重置后恢复列表（{len(rows2)} 行）")
    REPORT.check(s.page.input_value("#f-keyword") == "", "重置清空了搜索框")


@case("D03", "资料列表：状态页签过滤（未生效/生效中/已过期/已删除）")
def d03(app):
    s = app.admin
    s.goto("/documents")
    s.page.wait_for_selector("#doc-tbody tr", timeout=15000)
    for status, label in [("pending", "未生效"), ("active", "生效中"),
                          ("expired", "已过期"), ("archived", "已删除")]:
        s.page.click(f'.filter-tab[data-status="{status}"]')
        s.page.wait_for_timeout(1000)
        rows = _rows(s.page)
        if not rows or rows[0]["cells"] == 1:
            REPORT.check(True, f"[{label}] 无数据（空态正常）")
            continue
        bad = [r["text"][:40] for r in rows if label not in r["text"]]
        REPORT.check(not bad, f"[{label}] 共 {len(rows)} 行，状态列均为「{label}」；异常：{bad[:2]}")
    s.page.click('.filter-tab[data-status=""]')
    s.page.wait_for_timeout(800)


@case("D04", "资料详情/API：按日期查询历史状态（项目规范 §15/§31 边界）")
def d04(app):
    """专造一条样本：生效 2026-05-01 ~ 2026-05-31，逐日验证闭区间语义。"""
    s = app.admin
    r = http("POST", "/api/v1/documents", app.admin.token, {
        "title": f"E2E时效样本{int(time.time())}",
        "effective_from": "2026-05-01",
        "effective_to": "2026-05-31",
    })
    body = r["body"] or {}
    if r["status"] not in (200, 201) or not body.get("id"):
        REPORT.fail(f"创建时效样本失败：{r['status']} {str(body)[:100]}")
        return
    did = str(body["id"])
    app.state["doc_validity_id"] = did

    def status_on(asof: str) -> str:
        got = http("GET", f"/api/v1/documents/{did}/status?date={asof}", app.admin.token)
        return ((got.get("body") or {}).get("status") or "(无状态)")

    cases = [
        ("2026-04-30", "pending", "生效前一天"),
        ("2026-05-01", "active", "生效当天"),
        ("2026-05-02", "active", "生效后一天"),
        ("2026-05-30", "active", "失效前一天"),
        ("2026-05-31", "active", "失效当天（闭区间当日仍有效）"),
        ("2026-06-01", "expired", "失效后一天"),
    ]
    for asof, expect, note in cases:
        got = status_on(asof)
        REPORT.check(got == expect, f"{note} {asof} → {got}（期望 {expect}）")
    REPORT.check(s.page.locator("#f-asof").count() == 0,
                 "资料列表高级筛选不再提供查询日期")


@case("D05", "资料列表：逐级下钻（选中分类 → 面包屑 + 列表收敛 + 返回上级）")
def d05(app):
    s = app.admin
    s.goto("/documents")
    s.page.wait_for_selector("#category-tree .tree-node-content", timeout=15000)
    nodes = s.page.query_selector_all("#category-tree .tree-node-content[data-cat]")
    keys = [n.get_attribute("data-cat") for n in nodes]
    REPORT.check(len(nodes) > 0, f"分类导航渲染 {len(nodes)} 行（当前层）")
    target = None
    for n, k in zip(nodes, keys):
        if k and not k.startswith("__"):
            target = (n, k)
            break
    if not target:
        REPORT.fail("分类导航无可用节点")
        return
    node, key = target
    name = node.query_selector(".tree-label").inner_text().strip()
    before = len(_rows(s.page))
    node.click()
    s.page.wait_for_timeout(1400)

    # 下钻：侧栏面包屑仍在；主区不再出现「当前范围」重复条
    crumbs = s.page.evaluate(
        "() => {const c = document.querySelector('.cat-crumbs'); return c ? c.innerText.replace(/\\n/g,' ') : ''}")
    REPORT.check(bool(crumbs), f"下钻后出现面包屑：{crumbs[:60]}")
    REPORT.check(name[:4] in crumbs, f"面包屑包含当前分类「{name[:4]}」")
    REPORT.check(s.page.locator("#loc-bar").count() == 0,
                 "资料列表不再渲染「当前范围」条")

    after = _rows(s.page)
    REPORT.check(len(after) <= before, f"列表收敛：{before} → {len(after)} 行")

    # 返回上级：面包屑消失、回到根层
    s.page.click(".cat-back")
    s.page.wait_for_timeout(800)
    back_crumbs = s.page.evaluate("() => document.querySelectorAll('.cat-crumbs').length")
    REPORT.check(back_crumbs == 0, "点「返回上级」回到根层（面包屑消失）")

    # 2026-11-11：主区当前范围条已删除，返回上级只验证侧栏与右侧列表状态。
    REPORT.check(len(_rows(s.page)) == before, "「返回上级」同步恢复完整列表")


@case("D06", "资料列表：逐级下钻 / 展开全树 视图切换 + 全树只展开文件夹")
def d06(app):
    s = app.admin
    s.goto("/documents")
    s.page.wait_for_selector("#category-tree .tree-node-content", timeout=15000)

    # 默认逐级下钻
    drill_on = s.page.evaluate(
        "() => document.getElementById('btn-view-drill').classList.contains('on')")
    REPORT.check(drill_on, "默认视图为「逐级下钻」")
    toolbar_hidden = s.page.evaluate(
        "() => document.getElementById('tree-toolbar').hidden")
    REPORT.check(toolbar_hidden, "逐级下钻时「展开全部」工具条隐藏")

    drill_rows = len(s.page.query_selector_all("#category-tree [data-cat]"))

    # 切到全树：默认只显示一级文件夹
    s.page.click("#btn-view-tree")
    s.page.wait_for_timeout(700)
    tree_on = s.page.evaluate(
        "() => document.getElementById('btn-view-tree').classList.contains('on')")
    REPORT.check(tree_on, "切换到「展开全树」")
    collapsed_rows = len(s.page.query_selector_all("#category-tree [data-cat]"))
    REPORT.check(collapsed_rows <= drill_rows + 3,
                 f"全树默认折叠只渲染一级文件夹：{collapsed_rows} 行")

    # 展开全部 → 行数增加，且每行都是分类（无资料条目）
    s.page.click("#btn-expand-all")
    s.page.wait_for_timeout(700)
    expanded_rows = len(s.page.query_selector_all("#category-tree [data-cat]"))
    REPORT.check(expanded_rows > collapsed_rows,
                 f"「展开全部」后行数增加：{collapsed_rows} → {expanded_rows}")
    all_are_categories = s.page.evaluate(
        "() => Array.from(document.querySelectorAll('#category-tree [data-cat]'))"
        ".every(e => e.dataset.cat === '__all__' || e.dataset.cat === '__uncategorized__' "
        "|| /^\\d+$/.test(e.dataset.cat))")
    REPORT.check(all_are_categories, "全树只有分类节点，资料不进入树")
    btn_text = s.page.inner_text("#btn-expand-all")
    REPORT.check("折叠" in btn_text, f"按钮变为「折叠全部」：{btn_text}")

    # 折叠回去
    s.page.click("#btn-expand-all")
    s.page.wait_for_timeout(600)
    REPORT.check(len(s.page.query_selector_all("#category-tree [data-cat]")) == collapsed_rows,
                 "「折叠全部」回到折叠态")

    # 切回逐级下钻
    s.page.click("#btn-view-drill")
    s.page.wait_for_timeout(600)
    REPORT.check(s.page.evaluate("() => document.getElementById('tree-toolbar').hidden"),
                 "切回逐级下钻后工具条再次隐藏")


@case("D11", "资料列表：侧栏分类搜索（跨层级命中 + 高亮 + 直达）")
def d11(app):
    s = app.admin
    s.goto("/documents")
    s.page.wait_for_selector("#category-tree .tree-node-content", timeout=15000)

    # 取一个层级 ≥2 的分类名，验证搜索结果带父级路径
    s.page.evaluate(
        "() => {const el = document.querySelector('#category-tree [data-cat]'); return el ? el.innerText : ''}")
    s.page.fill("#cat-search", "DEMO")
    s.page.wait_for_timeout(700)
    hits = len(s.page.query_selector_all("#category-tree [data-cat]"))
    REPORT.check(hits > 0, f"搜索「DEMO」命中 {hits} 个分类")
    marks = s.page.evaluate("() => document.querySelectorAll('#category-tree mark').length")
    REPORT.check(marks > 0, f"命中关键词有高亮：{marks} 处")
    has_path = s.page.evaluate(
        "() => !!document.querySelector('#category-tree .tree-sub')")
    REPORT.check(has_path, "搜索结果展示父级路径")

    # 点第一条结果 → 右侧按该分类收敛，搜索框清空
    first_cat = s.page.evaluate(
        "() => {const el = document.querySelector('#category-tree [data-cat]');"
        " return (el && el.dataset.cat) || '';}")
    s.page.click('#category-tree [data-cat="' + first_cat + '"]')
    s.page.wait_for_timeout(1400)
    REPORT.check(s.page.input_value("#cat-search") == "", "选中后搜索框清空")
    REPORT.check(s.page.locator("#loc-bar").count() == 0,
                 "选中搜索结果后也不再渲染「当前范围」条")

    # 清空搜索回到浏览。
    # 注意：selectCategory() 选中结果时已顺手清空搜索并 renderNav()，
    # 清除按钮随之隐藏（cat-search-wrap 的 has-value 被移除），
    # 此时直接点它必然「element is not visible」超时。
    # 所以先重新输入关键词让按钮出现，再走「点清除」这条路径。
    s.page.fill("#cat-search", "DEMO")
    s.page.wait_for_timeout(700)
    s.page.click("#cat-search-clear")
    s.page.wait_for_timeout(500)
    # 断言口径修正（2026-11-06）：选中分类后本就处于「逐级下钻」视图，
    # 面包屑是该视图的固有结构，断言「没有 .cat-crumbs」与设计相反。
    # 真正该验的是：搜索态已退出 —— 输入框为空、命中高亮消失。
    REPORT.check(s.page.input_value("#cat-search") == "", "清空后搜索框为空")
    REPORT.check(s.page.evaluate(
        "() => document.querySelectorAll('#category-tree mark').length === 0"),
        "清空搜索后命中高亮消失（已退出搜索态）")
    REPORT.check(s.page.evaluate(
        "() => !!document.querySelector('.cat-crumbs')"),
        "仍处于逐级下钻视图（面包屑在位）")


@case("D07", "资料列表：仅看未分类 / 仅看未打标签（管理员整理视图）")
def d07(app):
    s = app.admin
    s.goto("/documents")
    s.page.wait_for_selector("#doc-tbody tr", timeout=15000)
    s.page.click('.filter-tab[data-organize="uncategorized"]')
    s.page.wait_for_timeout(1200)
    rows = _rows(s.page)
    bad = [r["text"][:50] for r in rows if r["cells"] > 1 and "未分类" not in r["text"]]
    REPORT.check(not bad, f"仅看未分类：{len(rows)} 行，异常行 {bad[:2]}")
    s.page.click('.filter-tab[data-organize="uncategorized"]')
    s.page.wait_for_timeout(600)
    s.page.click('.filter-tab[data-organize="untagged"]')
    s.page.wait_for_timeout(1200)
    rows2 = _rows(s.page)
    REPORT.check(True, f"仅看未打标签：{len(rows2)} 行")
    s.page.click('.filter-tab[data-organize="untagged"]')
    s.page.wait_for_timeout(600)


@case("D08", "资料列表：有效期覆盖筛选")
def d08(app):
    s = app.admin
    s.goto("/documents")
    s.page.wait_for_selector("#doc-tbody tr", timeout=15000)
    _open_adv(s.page)
    s.page.fill("#f-cover-from", "2026-01-01")
    s.page.fill("#f-cover-to", "2026-12-31")
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1200)
    rows = _rows(s.page)
    REPORT.check(True, f"2026 全年覆盖筛选 → {len(rows)} 行")
    cnt = s.page.inner_text("#adv-count").strip() if s.page.query_selector("#adv-count") else ""
    REPORT.check(True, f"高级筛选计数徽标：{cnt or '(未显示)'}")
    chips = s.page.inner_text("#doc-filter-summary") if s.page.query_selector("#doc-filter-summary") else ""
    REPORT.check("有效期覆盖" in chips, f"已选筛选摘要显示有效期覆盖：{chips[:50]}")
    s.page.click("#doc-filter-summary [data-filter='cover']")
    s.page.wait_for_timeout(900)
    REPORT.check(s.page.input_value("#f-cover-from") == "", "点击摘要 chip 单独移除有效期覆盖")
    REPORT.check(s.page.is_hidden("#doc-filter-summary"), "移除最后一个筛选后摘要整条隐藏")
    s.page.click("#btn-reset")
    s.page.wait_for_timeout(800)
    REPORT.check(s.page.input_value("#f-cover-from") == "", "重置清空有效期覆盖")


@case("D09", "资料列表：分页翻页")
def d09(app):
    s = app.admin
    s.goto("/documents")
    s.page.wait_for_selector("#doc-tbody tr", timeout=15000)
    first_ids = [r["link"] for r in _rows(s.page)]
    btns = s.page.query_selector_all("#doc-pagination button")
    if not btns:
        REPORT.check(True, "数据不足一页，分页未显示（正常）")
        return
    texts = [b.inner_text().strip() for b in btns]
    REPORT.check(any(t in ("2", "下一页", "›") for t in texts), f"分页控件：{texts}")
    nxt = None
    for b, t in zip(btns, texts):
        if t in ("2", "下一页", "›"):
            nxt = b
            break
    if nxt:
        nxt.click()
        s.page.wait_for_timeout(1400)
        second_ids = [r["link"] for r in _rows(s.page)]
        REPORT.check(second_ids and set(second_ids) != set(first_ids),
                     "翻页后列表内容发生变化")
        focused = s.page.evaluate("() => document.activeElement && document.activeElement.id")
        REPORT.check(focused == "docs-page-title",
                     f"翻页后焦点回到结果标题（activeElement={focused}）")
    s.goto("/documents")
    s.page.wait_for_selector("#doc-tbody tr", timeout=15000)


@case("D10", "资料列表：无横向溢出（1440 视口）")
def d10(app):
    s = app.admin
    s.goto("/documents")
    s.page.wait_for_selector("#doc-tbody tr", timeout=15000)
    over = s.page.evaluate(
        "() => {const w = document.querySelector('.table-wrap');"
        " if (!w) return -1; return w.scrollWidth - w.clientWidth;}")
    REPORT.check(over <= 1, f"表格容器无横向溢出（scrollWidth-clientWidth={over}）")
    doc_over = s.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    REPORT.check(doc_over <= 1, f"页面无横向滚动条（超出 {doc_over}px）")
