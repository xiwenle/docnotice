"""换肤专项走查（F 石青 · 左侧栏外壳）—— 真实浏览器断言 + 截图。

与 admin_journey.py 同套路（API 登录 + Cookie 注入，适配单点登录），
区别：本脚本只验证「视觉与外壳契约」，不碰业务数据、不做写操作。

断言分组：
  A 令牌层  body 底色 = 中性微凉 #f4f5f3；主色 = 石青 #2b5d8c；标题走衬线体
  B 外壳层  左侧栏存在、每页高亮项唯一且正确、未登录/普通用户不渲染管理段
  C 组件层  表头暖纸底、徽章有状态圆点、按钮圆角、卡片细边框、行分隔为虚线
  D 交互层  桌面收起为窄栏并跨刷新记忆；窄屏抽屉可开可关；顶条搜索跳转与「/」聚焦
  E 回归层  全部页面浏览器控制台零 error

用法：先启动 uvicorn（127.0.0.1:8000），再
  python scripts/theme_check.py
截图输出到 screenshots/theme/。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
ADMIN = {"username": "admin_demo", "password": "Demo@123456"}
NORMAL = {"username": "user_rnd_1", "password": "Demo@123456"}
SHOT_DIR = Path(__file__).parent.parent / "screenshots" / "theme"
SHOT_DIR.mkdir(parents=True, exist_ok=True)

# 期望令牌（与 style.css :root 一致）
# 2026-09-29 配色改版（F 石青）同步：只更新期望值，断言逻辑与用例数一字未动。
# 常量名一并从 INK_GREEN 改为 INK_PRIMARY —— 主色已不是墨绿，旧名字会误导下一个人
# （上一版换肤就吃过"名字对不上实际值"的亏）。
PAPER = "rgb(244, 245, 243)"        # --bg #f4f5f3（中性微凉）
INK_PRIMARY = "rgb(43, 93, 140)"    # --primary #2b5d8c（石青）
TH_BG = "rgb(233, 236, 230)"        # --bg-th #e9ece6
CARD_BORDER = "rgb(223, 227, 230)"  # --border #dfe3e6

# (路径, 期望高亮的侧栏项文本, 是否截图)
ADMIN_PAGES = [
    ("/", "工作台", True),
    ("/documents", "资料", True),
    ("/notices", "通知", True),
    ("/admin/documents/new", "新建资料", True),
    ("/admin/notices/new", "新建通知", True),
    ("/admin/categories", "分类管理", True),
    ("/admin/tags", "标签管理", True),
    ("/admin/notice-types", "通知类型", True),
    ("/admin/recycle-bin", "回收站", True),
    ("/admin/users", "用户管理", True),
    ("/admin/departments", "部门管理", True),
    ("/admin/backups", "备份恢复", True),
    ("/admin/audit-logs", "操作日志", True),
    ("/change-password", "", True),
]

RESULTS: list[tuple[bool, str]] = []


def record(ok: bool, msg: str) -> None:
    RESULTS.append((ok, msg))
    print(f"  {'PASS' if ok else 'FAIL'} {msg}")


def api(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def login_ctx(ctx, creds: dict) -> str:
    token = api("POST", "/api/v1/auth/login", "", creds)["access_token"]
    ctx.add_cookies([{"name": "access_token", "value": token, "url": BASE_URL}])
    return token


def goto(page, url: str) -> None:
    page.goto(BASE_URL + url)
    page.wait_for_load_state("networkidle")


def shot(page, filename: str) -> None:
    page.screenshot(path=str(SHOT_DIR / filename), animations="disabled")
    print(f"  📷 {filename}")


def css(page, selector: str, prop: str, pseudo: str | None = None) -> str:
    """取计算样式（pseudo 传 '::before' 可读伪元素）。"""
    return page.evaluate(
        "([sel, prop, pseudo]) => {"
        "  const el = document.querySelector(sel);"
        "  if (!el) return '__MISSING__';"
        "  return getComputedStyle(el, pseudo || null).getPropertyValue(prop);"
        "}",
        [selector, prop, pseudo],
    )


# ---------------------------------------------------------------------------
def section_a_tokens(page) -> None:
    print("\n[A] 令牌层：暖纸底 + 墨绿主色 + 衬线题")
    goto(page, "/")
    bg = css(page, "body", "background-color")
    record(bg == PAPER, f"body 底色 = {bg}（期望中性微凉 {PAPER}）")

    # 页面上任意一个主按钮（工作台有「＋ 新建资料」）
    prim = css(page, ".btn-primary", "background-color")
    record(prim == INK_PRIMARY, f"主按钮底色 = {prim}（期望石青 {INK_PRIMARY}）")

    fam = css(page, ".page-header h1", "font-family")
    record("serif" in fam.lower(), f"页面标题走衬线体：{fam[:70]}")

    fam_body = css(page, "body", "font-family")
    record("PingFang" in fam_body or "sans" in fam_body.lower(),
           f"正文仍走无衬线体（保证可读性）：{fam_body[:60]}")

    # 旧的冷蓝是否彻底清掉
    leftover = page.evaluate(
        "() => {"
        "  const bad = ['rgb(37, 99, 235)', 'rgb(248, 250, 252)', 'rgb(226, 232, 240)'];"
        "  const hits = [];"
        "  document.querySelectorAll('body *').forEach(el => {"
        "    const cs = getComputedStyle(el);"
        "    ['backgroundColor', 'color', 'borderTopColor'].forEach(p => {"
        "      if (bad.includes(cs[p])) hits.push(el.className + ':' + p + '=' + cs[p]);"
        "    });"
        "  });"
        "  return hits.slice(0, 8);"
        "}"
    )
    record(not leftover, f"无冷蓝残留（原 #2563eb/#f8fafc/#e2e8f0）：{leftover or '干净'}")


def section_b_shell(page) -> None:
    print("\n[B] 外壳层：左侧栏 + 逐页高亮")
    total = css(page, ".sb-nav", "display")
    record(total != "__MISSING__", f"左侧栏导航容器存在（display={total}）")

    for path, expect, _ in ADMIN_PAGES:
        goto(page, path)
        info = page.evaluate(
            "() => {"
            "  const items = Array.from(document.querySelectorAll('.sb-item'));"
            "  const on = items.filter(i => i.classList.contains('on'));"
            "  return {"
            "    n: items.length,"
            "    onCount: on.length,"
            "    onText: on.map(i => i.textContent.trim())[0] || '',"
            "    crumb: (document.querySelector('.crumbbar .cur') || {}).textContent || '',"
            "    menuBtn: !!document.querySelector('.menu-btn'),"
            "    hasAdminSec: Array.from(document.querySelectorAll('.sb-sec'))"
            "      .some(s => s.textContent.includes('管理')),"
            "  };"
            "}"
        )
        ok_on = info["onCount"] == (1 if expect else 0) and (not expect or expect in info["onText"])
        record(ok_on, f"{path} 高亮={info['onText'] or '(无)'}（期望 {expect or '无'}，命中 {info['onCount']} 项，共 {info['n']} 项）")

    # 面包屑顶条与当前页标题一致
    goto(page, "/admin/categories")
    crumb = page.locator(".crumbbar .cur").inner_text().strip()
    record(crumb == "分类管理", f"面包屑当前项 = {crumb}")


    # 面包屑唯一性：页面内自带的 .breadcrumb 必须隐藏，顶条带区段层级
    goto(page, "/admin/documents/new")
    bc = page.evaluate(
        "() => ({"
        "  inContent: (() => { const b = document.querySelector('.page-main .breadcrumb');"
        "                     return b ? getComputedStyle(b).display : 'absent'; })(),"
        "  crumb: (document.querySelector('.crumbbar') || {innerText: ''})"
        "           .innerText.replace(/\\s+/g, ' ').trim(),"
        "})"
    )
    record(bc["inContent"] in ("none", "absent"),
           f"页面内自带面包屑已隐藏，不出现两遍（display={bc['inContent']}）")
    record("管理" in bc["crumb"] and "新建资料" in bc["crumb"],
           f"顶条面包屑含区段层级：{bc['crumb']}")

    # 列表页自身不应显示重复的区段层级（如「资料 / 资料列表」）；
    # 排除右侧 .cb-right（列表页会出现「搜索资料」全局入口，不属于面包屑）
    goto(page, "/documents")
    crumb_docs = page.evaluate(
        "() => {const c=document.querySelector('.crumbbar').cloneNode(true);"
        " const r=c.querySelector('.cb-right'); if(r) r.remove();"
        " return c.innerText.replace(/\\s+/g,' ').trim();}"
    )
    record(crumb_docs.count("资料") == 1,
           f"列表页面包屑不重复：{crumb_docs}")


def section_c_components(page) -> None:
    print("\n[C] 组件层：表头 / 徽章 / 按钮 / 卡片 / 行分隔")
    # 用户管理页是标准表格页
    goto(page, "/admin/users")
    page.wait_for_selector("#user-tbody tr", timeout=10000)

    th_bg = css(page, ".table th", "background-color")
    record(th_bg == TH_BG, f"表头底色 = {th_bg}（期望 {TH_BG}）")

    td_border = css(page, ".table td", "border-bottom")
    record("dashed" in td_border, f"行分隔为虚线：{td_border}")

    badge_dot = page.evaluate(
        "() => {"
        "  const b = document.querySelector('.badge');"
        "  if (!b) return null;"
        "  const cs = getComputedStyle(b, '::before');"
        "  return {content: cs.content, w: cs.width, shape: cs.borderRadius};"
        "}"
    )
    record(bool(badge_dot) and badge_dot["w"] == "5px",
           f"徽章前置状态圆点：{badge_dot}")

    radius = css(page, ".btn", "border-radius")
    record(radius == "5px", f"按钮圆角 = {radius}（期望 5px）")

    card_border = css(page, ".card", "border-top-color") if page.locator(".card").count() else ""
    record(card_border in ("", CARD_BORDER), f"卡片边框色 = {card_border or '(本页无卡片)'}")

    # 主色被用作表格行内链接色（资料列表）
    goto(page, "/documents")
    page.wait_for_selector("#doc-tbody tr", timeout=10000)
    link = css(page, ".table a", "color")
    record(link == INK_PRIMARY, f"表格内链接色 = {link}（期望石青）")


def section_d_interactions(page) -> None:
    print("\n[D] 交互层：窄栏记忆 / 窄屏抽屉 / 顶条搜索")
    page.set_viewport_size({"width": 1440, "height": 900})
    goto(page, "/documents")

    width_full = page.evaluate("() => Math.round(document.querySelector('.side').getBoundingClientRect().width)")
    record(width_full == 192, f"侧栏常规宽度 = {width_full}px（期望 192）")

    page.click("#sb-collapse")
    page.wait_for_timeout(300)
    rail = page.evaluate(
        "() => ({cls: document.body.classList.contains('rail'),"
        " w: Math.round(document.querySelector('.side').getBoundingClientRect().width)})"
    )
    record(rail["cls"] and rail["w"] == 64, f"点收起 → 窄栏 {rail['w']}px（body.rail={rail['cls']}）")
    shot(page, "10_rail_collapsed.png")

    page.reload()
    page.wait_for_load_state("networkidle")
    persisted = page.evaluate("() => document.body.classList.contains('rail')")
    record(persisted, "刷新后仍保持窄栏（localStorage 记忆生效）")

    page.click("#sb-collapse")
    page.wait_for_timeout(300)
    record(page.evaluate("() => !document.body.classList.contains('rail')"), "再点一次恢复常规宽度")

    # 窄屏抽屉
    page.set_viewport_size({"width": 480, "height": 900})
    page.wait_for_timeout(400)
    left_closed = page.evaluate("() => Math.round(document.querySelector('.side').getBoundingClientRect().left)")
    record(left_closed < 0, f"窄屏侧栏默认移出视口（left={left_closed}px）")
    shot(page, "11_mobile_closed.png")

    page.click("#menu-btn")
    page.wait_for_timeout(400)
    left_open = page.evaluate("() => Math.round(document.querySelector('.side').getBoundingClientRect().left)")
    record(left_open == 0, f"点 ☰ 抽屉展开（left={left_open}px）")
    shot(page, "12_mobile_drawer.png")

    page.click("#side-backdrop")
    page.wait_for_timeout(400)
    record(page.evaluate("() => !document.body.classList.contains('drawer')"), "点遮罩关闭抽屉")

    # 顶条搜索：只在没有页内搜索的页面出现（列表页由页内搜索接管）
    page.set_viewport_size({"width": 1440, "height": 900})
    goto(page, "/admin/backups")
    page.wait_for_selector("#backup-tbody tr", timeout=10000)
    page.click("#cb-q")
    page.fill("#cb-q", "制度")
    page.press("#cb-q", "Enter")
    page.wait_for_load_state("networkidle")
    # URL 里关键词是百分号编码的，比较前先解码（否则中文永远比不中）
    decoded = urllib.parse.unquote(page.url)
    record("/documents?keyword=" in decoded and "制度" in decoded,
           f"顶条搜索跳转 = {page.url.replace(BASE_URL, '')}")

    # 列表页：顶条隐藏，「/」聚焦页内搜索
    goto(page, "/admin/tags")
    page.wait_for_selector("#f-keyword", timeout=10000)
    record(page.is_hidden("#cb-search"), "列表页隐藏顶条搜索（避免两个搜索入口）")
    page.click("body")
    page.keyboard.press("/")
    page.wait_for_timeout(200)
    focused = page.evaluate("() => document.activeElement && document.activeElement.id")
    record(focused == "f-keyword", f"按「/」聚焦页内搜索（activeElement={focused}）")


TABLE_PAGES = [
    "/documents",
    "/admin/users",
    "/admin/audit-logs",
    "/admin/backups",
    "/admin/recycle-bin",
]


def section_h_table_overflow(page) -> None:
    """桌面端表格不得横向溢出容器。

    溢出会同时带来两个劣化：底部冒出横向滚动条、右侧「操作」列被压到换行。
    根因是 .table 默认 table-layout:auto，会忽略 th 上的 width/min-width——
    需要精确列宽的表必须用 .table-fixed + <colgroup>。
    """
    print("\n[H] 表格不得横向溢出容器（1440 视口）")
    for path in TABLE_PAGES:
        goto(page, path)
        page.wait_for_timeout(900)
        info = page.evaluate(
            "() => {"
            "  const wraps = Array.from(document.querySelectorAll('.table-wrap'));"
            "  if (!wraps.length) return null;"
            "  const worst = wraps.map(w => w.scrollWidth - w.clientWidth)"
            "                      .sort((a, b) => b - a)[0];"
            "  return {worst, n: wraps.length};"
            "}"
        )
        if not info:
            record(True, f"{path} 本页无表格，跳过")
            continue
        record(info["worst"] <= 2,
               f"{path} 最大横向溢出 {info['worst']}px（{info['n']} 个表格容器）")


def section_e_console(page, bucket: list) -> None:
    print("\n[E] 回归层：全部页面控制台零 error")
    for path, _, _ in ADMIN_PAGES:
        goto(page, path)
        page.wait_for_timeout(500)
    # 登录页（登出态）
    anon = page.context.browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    p2 = anon.new_page()
    p2.goto(BASE_URL + "/login")
    p2.wait_for_load_state("networkidle")
    shot(p2, "13_login.png")
    login_bg = p2.evaluate("() => {const el=document.querySelector('.login-page'); if(!el) return {color:'', image:''}; const s=getComputedStyle(el); return {color:s.backgroundColor, image:s.backgroundImage};}")
    record(login_bg["image"] not in ("", "none") or login_bg["color"] not in ("", "rgba(0, 0, 0, 0)"), f"登录页自定义背景 = {login_bg}")
    record(p2.locator(".side").count() == 0, "登录页不渲染左侧栏")
    p2.close()
    anon.close()

    errs = [b for b in bucket if b.startswith("[pageerror]") or "[console.error]" in b]
    record(not errs, f"控制台 error 数 = {len(errs)}")
    for e in sorted(set(errs))[:10]:
        print("       " + e)


def column_alignment(page, label: str) -> None:
    """表头列数必须 == 数据行单元格数。

    这是 2026-09-14 换肤踩到的真实缺陷的守卫：四个前端模块曾用
    `!!document.querySelector('#nav-admin-dropdown')` 探测身份，顶栏被换成
    左侧栏后管理员被当成普通用户 → 资料列表不渲染选择列 → 表头 6 列、行 5 格，
    整行内容左移一列（标题挤成逐字换行）。后端测试不校验 DOM，抓不到。
    """
    goto(page, "/documents")
    page.wait_for_selector("#doc-tbody tr", timeout=15000)
    info = page.evaluate(
        "() => {"
        "  const heads = document.querySelectorAll('.table thead th').length;"
        "  const rows = Array.from(document.querySelectorAll('#doc-tbody tr'));"
        "  const dataRow = rows.find(r => !r.querySelector('td[colspan]'));"
        "  return {"
        "    heads,"
        "    cells: dataRow ? dataRow.children.length : -1,"
        "    hasCheck: !!document.querySelector('#doc-tbody .doc-check'),"
        "    hasMore: !!document.querySelector('#doc-tbody .more-menu'),"
        "    headerHasCheck: !!document.querySelector('#doc-check-all'),"
        "    titleW: (() => { const a = document.querySelector('#doc-tbody .doc-title-link');"
        "                    return a ? Math.round(a.getBoundingClientRect().width) : -1; })(),"
        "    titleLineBoxes: (() => { const a = document.querySelector('#doc-tbody .doc-title-link');"
        "                    return a ? a.getClientRects().length : -1; })(),"
        "    cellW: (() => { const td = document.querySelector('#doc-tbody .doc-title-link');"
        "                    return td && td.closest('td') ? Math.round(td.closest('td').getBoundingClientRect().width) : -1; })(),"
        "  };"
        "}"
    )
    record(info["heads"] == info["cells"],
           f"[{label}] 表头 {info['heads']} 列 == 数据行 {info['cells']} 格（列不得错位）")
    return info


def section_g_admin_ui(page) -> None:
    print("\n[G] 管理员特权 UI：选择列 / ⋯ 菜单 / 列不错位")
    info = column_alignment(page, "管理员")
    record(info["hasCheck"] and info["headerHasCheck"],
           f"管理员可见选择列（表头全选={info['headerHasCheck']} 行内复选框={info['hasCheck']}）")
    record(info["hasMore"], f"管理员可见行内「⋯」编辑/删除菜单 = {info['hasMore']}")
    # 行内 <a> 的宽度 = 文字宽度，不能用来判断列宽；判断"有没有被挤到换行"才对：
    # 单行时 getClientRects() 只返回 1 个行框，错位挤窄时会返回多个。
    record(info["titleLineBoxes"] == 1,
           f"标题渲染为 1 行（行框数={info['titleLineBoxes']}，所在单元格宽 {info['cellW']}px）")
    shot(page, "15_admin_documents_columns.png")


def section_f_normal_user(p, browser) -> None:
    print("\n[F] 权限层：普通用户侧栏不出现管理段")
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    login_ctx(ctx, NORMAL)
    page = ctx.new_page()
    goto(page, "/")
    info = page.evaluate(
        "() => ({"
        "  n: document.querySelectorAll('.sb-item').length,"
        "  secs: Array.from(document.querySelectorAll('.sb-sec')).map(s => s.textContent.trim()),"
        "  role: (document.querySelector('.role-tag') || {}).textContent || '',"
        "})"
    )
    record("管理" not in "".join(info["secs"]),
           f"普通用户侧栏段 = {info['secs']}（不应含管理段），共 {info['n']} 项")
    record("普通用户" in info["role"], f"侧栏底部角色标签 = {info['role'].strip()}")
    shot(page, "14_normal_user_sidebar.png")

    # 普通用户：列数同样必须自洽（只是少一列选择列）
    info2 = column_alignment(page, "普通用户")
    record(not info2["hasCheck"] and not info2["headerHasCheck"],
           "普通用户无选择列（表头与行内一致地不渲染）")
    ctx.close()


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900}, locale="zh-CN", device_scale_factor=2
        )
        page = ctx.new_page()
        bucket: list[str] = []
        page.on("console", lambda m: bucket.append(f"[console.{m.type}] {m.text}")
                if m.type == "error" else None)
        page.on("pageerror", lambda e: bucket.append(f"[pageerror] {e}"))

        print("\n[0] 登录 admin_demo（API 登录 + Cookie 注入，适配单点登录）")
        login_ctx(ctx, ADMIN)
        record(True, "已注入会话 Cookie")

        section_a_tokens(page)
        section_b_shell(page)
        section_c_components(page)
        section_g_admin_ui(page)
        section_h_table_overflow(page)
        section_d_interactions(page)

        print("\n[B2] 补齐各页截图（用于人工核对观感）")
        for path, _, want_shot in ADMIN_PAGES:
            if want_shot:
                goto(page, path)
                page.wait_for_timeout(600)
                name = (path.strip("/").replace("/", "_") or "dashboard")
                shot(page, f"0_{name}.png")

        section_f_normal_user(p, browser)
        section_e_console(page, bucket)

        print("\n" + "=" * 64)
        ok_n = sum(1 for ok, _ in RESULTS if ok)
        for ok, msg in RESULTS:
            print(f"  {'PASS' if ok else 'FAIL'} {msg}")
        print(f"\n共 {len(RESULTS)} 项：PASS {ok_n} / FAIL {len(RESULTS) - ok_n}")
        print(f"截图目录：{SHOT_DIR}")
        browser.close()

    sys.exit(0 if ok_n == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
