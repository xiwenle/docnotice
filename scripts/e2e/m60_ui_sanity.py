"""M60 UI 一致性：换肤后的外壳契约、左侧栏高亮唯一性、面包屑唯一性、表格不溢出。

2026-09-14 换肤（素卷墨绿 + 左侧栏）后新增的结构性检查，防止后续改动再次引入
「顶栏与页面双面包屑」「侧栏不高亮/高亮多个」「窄屏横向滚动」等回归。
"""
from __future__ import annotations

from harness import REPORT, SHOT_DIR, case

# 路径 → (期望高亮的侧栏项 label, 期望面包屑当前页文案)
# 第三项是期望的面包屑「当前页」文案；列表页刻意带「列表」后缀（与侧栏「资料」区分，
# 见 pages.py::_nav_section 的防重复说明），非缺陷。
PAGES = [
    ("/", "工作台", None),
    ("/documents", "资料", "资料列表"),
    ("/notices", "通知", "通知列表"),
    ("/admin/documents/new", "新建资料", "新建资料"),
    ("/admin/notices/new", "新建通知", "新建通知"),
    ("/admin/categories", "分类管理", "分类管理"),
    ("/admin/tags", "标签管理", "标签管理"),
    ("/admin/notice-types", "通知类型", "通知类型"),
    ("/admin/recycle-bin", "回收站", "回收站"),
    ("/admin/users", "用户管理", "用户管理"),
    ("/admin/departments", "部门管理", "部门管理"),
    ("/admin/backups", "备份恢复", "备份恢复"),
    ("/admin/audit-logs", "操作日志", "操作日志"),
]


@case("V01", "侧栏：每条管理路由有且仅有一个高亮项，且是当前页")
def v01(app):
    s = app.admin
    bad = []
    for path, label, _ in PAGES:
        s.goto(path)
        s.page.wait_for_timeout(700)
        on = s.page.eval_on_selector_all(
            ".sb-item.on .lbl", "els => els.map(e => e.textContent.trim())")
        aria = s.page.eval_on_selector_all(
            ".sb-item[aria-current='page'] .lbl", "els => els.map(e => e.textContent.trim())")
        if on != [label]:
            bad.append(f"{path} 高亮 {on}（期望 [{label}]）")
        if aria != [label]:
            bad.append(f"{path} aria-current {aria}（期望 [{label}]）")
    REPORT.check(not bad, "全部 13 条路由侧栏高亮唯一且正确" + (f"；例外：{bad}" if bad else ""))


@case("V02", "面包屑：顶条唯一（不出现页面内第二套 .breadcrumb）")
def v02(app):
    s = app.admin
    dup = []
    for path, _, _ in PAGES:
        s.goto(path)
        s.page.wait_for_timeout(600)
        n_top = s.page.eval_on_selector_all(".crumbbar", "els => els.length")
        # 页面内自带的旧 .breadcrumb 必须隐藏（换肤约定：顶条是唯一面包屑）
        inner = s.page.eval_on_selector_all(
            ".page-main .breadcrumb",
            "els => els.filter(e => e.offsetParent !== null).map(e => e.textContent.trim())")
        if n_top != 1:
            dup.append(f"{path} 顶条数量 {n_top}")
        if inner:
            dup.append(f"{path} 页面内仍显示旧面包屑：{inner[:1]}")
    REPORT.check(not dup, "顶条唯一且无重复面包屑" + (f"；例外：{dup}" if dup else ""))


@case("V03", "面包屑：当前页文案与页面一致，管理段不可点")
def v03(app):
    s = app.admin
    bad = []
    for path, _, cur in PAGES:
        if cur is None:
            continue
        s.goto(path)
        s.page.wait_for_timeout(600)
        txt = s.page.eval_on_selector(".crumbbar .cur", "e => e.textContent.trim()")
        sec = s.page.eval_on_selector_all(
            ".crumbbar .sep ~ *", "els => els.length")
        if txt != cur:
            bad.append(f"{path} 当前页「{txt}」（期望「{cur}」）")
        # 管理段：第二级是纯文字，不应是可点链接
        if path.startswith("/admin/") and sec:
            has_link = s.page.eval_on_selector_all(
                ".crumbbar a", "els => els.map(e => e.textContent.trim())")
            if "管理" in has_link:
                bad.append(f"{path} 「管理」段被渲染为可点链接")
    REPORT.check(not bad, "面包屑文案与层级正确" + (f"；例外：{bad}" if bad else ""))


@case("V04", "布局：六种宽度无横向滚动；≤900 资料表卡片化")
def v04(app):
    s = app.admin
    bad = []
    # 390 为 2026-10-19 视觉走查补入：docs-layout 纵向堆叠时若不 stretch，
    # docs-main 会被 640px 宽表撑开、整页布局视口扩到 654px（移动端被缩小）。
    # 900 同批补入：旧卡片断点 600px 下，601–900 主区（495–628px）小于宽表
    # 最小宽度 640px，平板/横屏手机会出现 26–145px 表内横滚。
    for width in (1440, 1280, 1024, 900, 768, 390):
        s.page.set_viewport_size({"width": width, "height": 900})
        for path, _, _ in PAGES:
            s.goto(path)
            s.page.wait_for_timeout(500)
            over = s.page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            if over > 2:
                bad.append(f"{width}px {path} 横向溢出 {over}px")
    REPORT.check(not bad, "六种宽度 × 13 个页面均无横向溢出" + (f"；例外：{bad[:3]}" if bad else ""))

    # 卡片断点回归：900/768 表格转块级卡片，1024 保持真实表格行
    for width, expected in ((900, "block"), (768, "block"), (1024, "table-row")):
        s.page.set_viewport_size({"width": width, "height": 900})
        s.goto("/documents")
        s.page.wait_for_selector("#doc-tbody tr", timeout=15000)
        s.page.wait_for_timeout(700)
        display = s.page.eval_on_selector("#doc-tbody tr", "el => getComputedStyle(el).display")
        REPORT.check(display == expected, f"{width}px 资料行 display={display}（期望 {expected}）")

    # 901–1199：分类树置顶，表格全宽 + 表头 sticky；1200 起恢复左右并排
    for width, direction in ((1024, "column"), (1200, "row")):
        s.page.set_viewport_size({"width": width, "height": 900})
        s.goto("/documents")
        s.page.wait_for_selector("#doc-tbody tr", timeout=15000)
        s.page.wait_for_timeout(700)
        info = s.page.evaluate(
            "() => {const layout=document.querySelector('.docs-layout');"
            " const wrap=document.querySelector('.docs-main .table-wrap');"
            " const th=document.querySelector('.docs-main .table thead th');"
            " return {dir:getComputedStyle(layout).flexDirection,"
            "  over:wrap.scrollWidth-wrap.clientWidth,"
            "  sticky:getComputedStyle(th).position};}")
        REPORT.check(info["dir"] == direction,
                     f"{width}px 资料布局方向={info['dir']}（期望 {direction}）")
        REPORT.check(info["over"] <= 1, f"{width}px 资料表无需横向滚动（溢出 {info['over']}px）")
        REPORT.check(info["sticky"] == "sticky", f"{width}px 表头 sticky")
    s.page.set_viewport_size({"width": 1440, "height": 900})


@case("V05", "表格：宽表不撑破容器（.table-wrap 自身滚动而非整页滚动）")
def v05(app):
    s = app.admin
    s.page.set_viewport_size({"width": 1024, "height": 900})
    bad = []
    for path in ("/admin/audit-logs", "/admin/recycle-bin", "/admin/users",
                 "/documents", "/admin/backups"):
        s.goto(path)
        s.page.wait_for_timeout(900)
        info = s.page.evaluate(
            "() => Array.from(document.querySelectorAll('.table-wrap')).map(w => ({"
            "  overflowX: getComputedStyle(w).overflowX,"
            "  over: w.scrollWidth - w.clientWidth,"
            "  wider: w.getBoundingClientRect().width > document.documentElement.clientWidth"
            "}))")
        for w in info:
            if w["wider"]:
                bad.append(f"{path} 表格容器超出视口")
        if not info:
            bad.append(f"{path} 未找到 .table-wrap")
    s.page.set_viewport_size({"width": 1440, "height": 900})
    REPORT.check(not bad, "表格容器在窄屏内自滚动" + (f"；例外：{bad}" if bad else ""))


@case("V06", "主题令牌：亮色主题变量生效且文字与背景对比可读")
def v06(app):
    s = app.admin
    s.goto("/documents")
    s.page.wait_for_selector("#doc-tbody tr", timeout=12000)
    tok = s.page.evaluate(
        "() => {const cs = getComputedStyle(document.documentElement);"
        " return {bg: cs.getPropertyValue('--bg-page').trim() || cs.getPropertyValue('--bg').trim(),"
        "  card: cs.getPropertyValue('--bg-card').trim(),"
        "  primary: cs.getPropertyValue('--primary').trim(),"
        "  text: cs.getPropertyValue('--text').trim() || cs.getPropertyValue('--text-1').trim()};}")
    REPORT.check(bool(tok["card"]) and bool(tok["primary"]),
                 f"设计令牌已注入：card={tok['card']} primary={tok['primary']}")

    pair = s.page.evaluate(
        "() => {const el = document.querySelector('.page-main');"
        " if (!el) return null;"
        " const cs = getComputedStyle(el);"
        " return {fg: cs.color, bg: getComputedStyle(document.body).backgroundColor};}")
    if pair:
        REPORT.check(bool(pair["fg"]) and bool(pair["bg"]),
                     f"正文取色可用：文字 {pair['fg']} / 背景 {pair['bg']}")
        # 亮色主题：正文必须深色（相对亮度低）
        rgb = s.page.evaluate(
            "(c) => {const m = c.match(/(\\d+), (\\d+), (\\d+)/);"
            " return m ? [ +m[1], +m[2], +m[3] ] : null;}", pair["fg"])
        if rgb:
            REPORT.check(sum(rgb) / 3 < 140, f"亮色主题正文为深色：rgb{tuple(rgb)}")
    s.page.screenshot(path=str(SHOT_DIR / "V06_theme.png"))


@case("V07", "侧栏折叠：切换后宽度变化且导航项仍可点")
def v07(app):
    s = app.admin
    s.goto("/documents")
    s.page.wait_for_timeout(800)
    items = s.page.eval_on_selector_all(".sb-item", "els => els.length")
    icons = s.page.eval_on_selector_all(".sb-item .sb-ico", "els => els.length")
    REPORT.check(items == icons == 13, f"侧栏 13 项均有细线图标（items={items}, icons={icons}）")
    w0 = s.page.eval_on_selector("#app-side", "e => e.getBoundingClientRect().width")
    s.page.click("#sb-collapse")
    s.page.wait_for_timeout(900)
    w1 = s.page.eval_on_selector("#app-side", "e => e.getBoundingClientRect().width")
    REPORT.check(abs(w1 - w0) > 8, f"折叠按钮改变了侧栏宽度：{w0:.0f} → {w1:.0f}")
    REPORT.check(s.page.is_visible(".sb-item.on .sb-ico"), "窄栏仍显示当前项图标")
    expanded = s.page.get_attribute("#sb-collapse", "aria-expanded")
    REPORT.check(expanded in ("true", "false"), f"aria-expanded 已同步：{expanded}")
    s.page.click("#sb-collapse")
    s.page.wait_for_timeout(900)
    w2 = s.page.eval_on_selector("#app-side", "e => e.getBoundingClientRect().width")
    REPORT.check(abs(w2 - w0) < 4, f"再次点击可还原宽度：{w2:.0f} ≈ {w0:.0f}")


@case("V08", "全部管理页：标题、无残留占位符、无 404 文案")
def v08(app):
    s = app.admin
    bad = []
    for path, label, _ in PAGES:
        s.goto(path)
        s.page.wait_for_timeout(900)
        body = s.page.inner_text("body")
        for kw in ("{{", "}}", "None", "undefined", "加载失败", "404"):
            if kw in body:
                bad.append(f"{path} 含可疑文案「{kw}」")
        if not body.strip():
            bad.append(f"{path} 页面空白")
    REPORT.check(not bad, "13 个页面渲染完整无占位符残留" + (f"；例外：{bad[:3]}" if bad else ""))


@case("V09", "API 封装契约：get/post/put/del 都按传入路径发请求（防 path 被吞）")
def v09(app):
    """守卫用例：2026-09-14 曾因 api.post 少传 path，全站 POST 变成
    fetch([object Object])，登录/新建/备份等一切写操作静默失效。此处在真实浏览器
    里逐个调用四类方法，断言请求确实打到传入路径。"""
    app.allow_noise()  # 本用例故意请求不存在的路径（就是要看 URL 有没有被吞），404 属预期
    s = app.admin
    s.goto("/")
    s.page.wait_for_selector("#recent-documents", timeout=15000)
    seen = s.page.evaluate("""async () => {
      const seen = [];
      const orig = window.fetch;
      window.fetch = function (u, o) {
        seen.push(String(u && u.url ? u.url : u));
        return orig.apply(this, arguments);
      };
      const call = async (fn) => { try { await fn(); } catch (e) { /* 预期 404 */ } };
      const p = '/api/v1/__path_guard__';
      await call(() => api.get(p));
      await call(() => api.post(p, { a: 1 }));
      await call(() => api.put(p, { a: 1 }));
      await call(() => api.del(p, { a: 1 }));
      window.fetch = orig;
      return seen;
    }""")
    hit = [u for u in seen if "__path_guard__" in u]
    REPORT.check(len(hit) == 4, f"四类方法各发出 1 次请求到指定路径（命中 {len(hit)}/4：{hit}）")
    broken = [u for u in seen if "undefined" in u or "[object" in u]
    REPORT.check(not broken, f"无被吞掉/拼错的路径：{broken[:3]}")
