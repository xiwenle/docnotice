"""M02 权限边界（项目规范 §33）：前端隐藏 ≠ 后端放行，逐条用浏览器实测。"""
from __future__ import annotations

from harness import BASE_URL, REPORT, SHOT_DIR, case

ADMIN_PAGES = [
    "/admin/documents/new",
    "/admin/notices/new",
    "/admin/users",
    "/admin/departments",
    "/admin/categories",
    "/admin/tags",
    "/admin/notice-types",
    "/admin/recycle-bin",
    "/admin/audit-logs",
    "/admin/backups",
]

# (方法, 管理员专属 API, 期望非 admin 状态码)
ADMIN_APIS = [
    ("POST", "/api/v1/users", {"username": "xsec1", "password": "Demo@123456", "display_name": "越权测试"}),
    ("GET", "/api/v1/users", None),
    ("GET", "/api/v1/audit-logs", None),
    ("POST", "/api/v1/categories", {"name": "越权分类x"}),
    ("POST", "/api/v1/tags", {"name": "越权标签x"}),
    ("POST", "/api/v1/departments", {"name": "越权部门x"}),
    ("POST", "/api/v1/notice-types", {"name": "越权类型x"}),
    ("POST", "/api/v1/backups", {}),
    ("GET", "/api/v1/recycle-bin", None),
    ("POST", "/api/v1/documents/bulk", {"ids": [1], "action": "archive"}),
]


@case("P01", "普通用户访问全部管理页 → 一律被挡回")
def p01(app):
    u = app.user
    blocked = []
    leaked = []
    for path in ADMIN_PAGES:
        u.goto(path)
        if "/login" in u.page.url or u.page.url.rstrip("/") == BASE_URL:
            blocked.append(path)
        else:
            leaked.append(f"{path} → {u.page.url}")
            u.page.screenshot(path=str(SHOT_DIR / f"P01_leak{path.replace('/', '_')}.png"))
    REPORT.check(not leaked, f"无管理页泄漏（泄漏：{leaked}）")
    REPORT.check(len(blocked) == len(ADMIN_PAGES), f"{len(blocked)}/{len(ADMIN_PAGES)} 个管理页被拦截")


@case("P02", "普通用户在页面内直调管理员 API → 后端 403（前端隐藏不够）")
def p02(app):
    app.allow_noise()  # 本用例主动触发 403，属预期噪声
    leaks = []
    for method, path, payload in ADMIN_APIS:
        try:
            r = app.user.fetch(method, path, payload)
        except Exception as e:  # noqa: BLE001
            REPORT.fail(f"调用异常 {method} {path}：{e}")
            continue
        if r["status"] not in (401, 403):
            leaks.append(f"{method} {path} → {r['status']}")
    REPORT.check(not leaks, f"全部管理员 API 被后端拦截（越权成功：{leaks}）")


@case("P03", "资料列表：普通用户不渲染选择列与管理员操作，且表头列数一致")
def p03(app):
    a = app.admin
    u = app.user
    a.goto("/documents")
    a.page.wait_for_selector("#doc-tbody tr", timeout=15000)
    admin_cols = a.page.evaluate(
        "() => document.querySelectorAll('#doc-tbody').length && "
        "Array.from(document.querySelectorAll('table thead th')).length")
    admin_cells = a.page.evaluate(
        "() => {const tr = document.querySelector('#doc-tbody tr');"
        " return tr ? tr.querySelectorAll('td').length : 0;}")
    admin_has_check = a.page.query_selector(".doc-check") is not None

    u.goto("/documents")
    u.page.wait_for_selector("#doc-tbody tr", timeout=15000)
    user_cols = u.page.evaluate(
        "() => Array.from(document.querySelectorAll('table thead th')).length")
    user_cells = u.page.evaluate(
        "() => {const tr = document.querySelector('#doc-tbody tr');"
        " return tr ? tr.querySelectorAll('td').length : 0;}")
    user_has_check = u.page.query_selector(".doc-check") is not None

    REPORT.check(admin_has_check and admin_cols == admin_cells,
                 f"管理员：有选择列且列数一致（表头 {admin_cols} / 行 {admin_cells}）")
    REPORT.check(not user_has_check, "普通用户：无选择列 checkbox")
    REPORT.check(user_cols == user_cells,
                 f"普通用户表头与单元格数一致（表头 {user_cols} / 行 {user_cells}）")
    REPORT.check(user_cols == admin_cols - 1,
                 f"普通用户比管理员少 1 列（{user_cols} vs {admin_cols}）")
    # 断言必须只看「操作列」，不能用整行 innerText：
    # 资料标题本身可能含「编辑/删除」字样（如 E2E批量删除0-…、E2E永久删除资料…），
    # 整行匹配会把这些标题误判成普通用户看到了管理入口（2026-09-15 修）。
    ops = u.page.evaluate(
        "() => Array.from(document.querySelectorAll('#doc-tbody tr')).some(tr => {"
        "  const tds = tr.querySelectorAll('td');"
        "  const last = tds[tds.length - 1];"
        "  if (!last) return false;"
        "  return /编辑|删除/.test(last.innerText)"
        "     || !!last.querySelector('a[href*=\"/edit\"], button.danger, [data-action=\"delete\"]');"
        "})")
    REPORT.check(not ops, "普通用户行内操作列无「编辑/删除」入口")


@case("P04", "通知列表：普通用户不得发起创建/取消通知")
def p04(app):
    app.allow_noise()
    u = app.user
    # 2026-11-04：POST /notices/{id}/publish 已移除（创建即发布），
    # 改为验证「创建」与「取消」两个管理动作对普通用户关闭。
    r0 = u.fetch("POST", "/api/v1/notices", {"title": "越权创建", "content": "x"})
    REPORT.check(r0["status"] in (401, 403), f"创建通知接口对普通用户 {r0['status']}")
    r2 = u.fetch("POST", "/api/v1/notices/1/cancel")
    REPORT.check(r2["status"] in (401, 403), f"取消通知接口对普通用户 {r2['status']}")
    u.goto("/notices")
    u.page.wait_for_timeout(1200)
    admin_only = u.page.evaluate(
        "() => document.body.innerText.includes('新建通知') || "
        "document.body.innerText.includes('阅读统计')")
    REPORT.check(not admin_only, "通知列表页未向普通用户展示管理员入口")


@case("P05", "资料 API：普通用户不可写（PUT/DELETE）")
def p05(app):
    app.allow_noise()
    # 取一个真实资料 id
    docs = app.user.fetch("GET", "/api/v1/documents?page=1&page_size=1")
    items = (docs["body"] or {}).get("items") or []
    if not items:
        REPORT.fail("无资料可用于越权测试")
        return
    did = items[0]["id"]
    r1 = app.user.fetch("PUT", f"/api/v1/documents/{did}", {"title": "被越权改名"})
    REPORT.check(r1["status"] in (401, 403), f"PUT /documents/{did} → {r1['status']}")
    r2 = app.user.fetch("DELETE", f"/api/v1/documents/{did}", {"reason": "越权"})
    REPORT.check(r2["status"] in (401, 403), f"DELETE /documents/{did} → {r2['status']}")
    # 确认没有被改掉
    chk = app.admin.fetch("GET", f"/api/v1/documents/{did}")
    title = (chk["body"] or {}).get("title", "")
    REPORT.check(title != "被越权改名", f"标题未被越权修改：{title[:30]}")


@case("P06", "静态资源不得暴露 data/ 目录（项目规范 §11）")
def p06(app):
    app.allow_noise()
    r = app.admin.fetch("GET", "/data/database/app.db")
    REPORT.check(r["status"] in (0, 404, 403, 401), f"/data/database/app.db → {r['status']}")
    r2 = app.admin.fetch("GET", "/static/../data/database/app.db")
    REPORT.check(r2["status"] in (0, 404, 403, 401), f"穿越路径 → {r2['status']}")
