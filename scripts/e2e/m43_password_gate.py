"""M43 首登改密服务端闸门（2026-10-16 P0 修复的 E2E 回归）。

为什么单独立模块：pytest 的 4 条闸门回归（test_auth_boundary.py::
TestMustChangePasswordGate）走的是 TestClient，够不着**真实浏览器**才验得了的两层：

- 登录即拦截：表单登录成功后 login.js 据登录响应里的 must_change_password
  标记把用户送到 /change-password；直接访问业务页时 base.html 的 meta refresh
  是第二道保险。api.js 新增的 PASSWORD_CHANGE_REQUIRED → 跳改密联动也在这里验。
- 改密后恢复：改密页提交 → 落工作台 → 业务 API 200 且控制台无错。
- API 层：持有效 JWT 调业务 API → 403 PASSWORD_CHANGE_REQUIRED；
  白名单（/auth/me、/auth/logout）放行；改密后旧令牌随 token_version 自增作废。

写法纪律（TESTING.md §六·1）：不固定 sleep 等异步——登录跳转、改密提交
一律 wait_for_url / wait_for_selector；预期 4xx 的 fetch 会在控制台留
"Failed to load resource"，按惯例 app.allow_noise() 显式声明。
"""
from __future__ import annotations

import re
import time

from harness import BASE_URL, REPORT, anon_session, api_login, case, http

GATE_PASSWORD = "Gate@123456"   # 初始密码（满足强度：≥8 位含字母数字）
NEW_PASSWORD = "Gate@765432"    # 改密后的新密码


def _create_flagged_user(app) -> dict:
    """管理员建一个 must_change_password=true 的临时用户（API 直连，无 UI 依赖）。"""
    uname = f"e2e_gate_{int(time.time())}"
    r = http(
        "POST", "/api/v1/users", app.admin.token,
        {
            "username": uname,
            "password": GATE_PASSWORD,
            "display_name": "临时改密闸门用户",
            "role": "user",
            "must_change_password": True,
        },
    )
    REPORT.check(r["status"] in (200, 201), f"创建带改密标记的临时用户：{r['status']}")
    uid = (r["body"] or {}).get("id")
    app.state["gate_user_id"] = uid
    app.state["gate_user_name"] = uname
    return {"id": uid, "username": uname, "password": GATE_PASSWORD}


def _form_login(page, username: str, password: str) -> None:
    """走真实登录表单（不注入 Cookie）。"""
    page.goto(BASE_URL + "/login")
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("#login-submit")


def _page_fetch(page, method: str, path: str):
    """页面上下文内 fetch（带 Cookie），用于验证「后端闸门」而非前端隐藏。"""
    return page.evaluate(
        "async ([m, p]) => {"
        "  const r = await fetch(p, {method: m, credentials: 'same-origin',"
        "               headers: {'Content-Type': 'application/json'}});"
        "  let j = null; try { j = await r.json(); } catch (e) {}"
        "  return {status: r.status, body: j};"
        "}",
        [method, path],
    )


@case("Y01", "改密闸门 API 层：标记用户持 JWT 调业务 API → 403，白名单放行")
def y01(app):
    u = _create_flagged_user(app)
    if not u["id"]:
        REPORT.fail("未取到临时用户 id，后续分支跳过")
        return
    token = api_login({"username": u["username"], "password": u["password"]})["access_token"]
    app.state["gate_token"] = token  # Y04 验旧令牌作废用

    # 白名单①：/auth/me 放行且带回标记（前端守卫据此跳转，base.html:10）
    me = http("GET", "/api/v1/auth/me", token)
    REPORT.check(me["status"] == 200, f"/auth/me 放行：{me['status']}")
    REPORT.check(
        (me["body"] or {}).get("must_change_password") is True,
        f"/auth/me 回报改密标记：{(me['body'] or {}).get('must_change_password')}",
    )

    # 业务 API 一律 403 + 专属错误码（修复前此处为 200——持 JWT 可绕过改密）
    docs = http("GET", "/api/v1/documents", token)
    REPORT.check(docs["status"] == 403, f"资料列表被拦：{docs['status']}")
    code = ((docs["body"] or {}).get("error") or {}).get("code")
    REPORT.check(code == "PASSWORD_CHANGE_REQUIRED", f"错误码正确：{code}")

    # 白名单②：登出放行（清 Cookie；JWT 本身仍在，但改密未完成前依旧被拦）
    out = http("POST", "/api/v1/auth/logout", token)
    REPORT.check(out["status"] == 200, f"/auth/logout 放行：{out['status']}")


@case("Y02", "浏览器层：标记用户登录即送改密页；直访业务页被 meta refresh 拦回")
def y02(app):
    uid = app.state.get("gate_user_id")
    uname = app.state.get("gate_user_name")
    if not uid:
        REPORT.fail("无临时用户（依赖 Y01）")
        return
    # 预期 403 fetch 会在控制台留 "Failed to load resource"——按惯例显式声明
    app.allow_noise()

    ctx, page = anon_session(app.browser)
    _form_login(page, uname, GATE_PASSWORD)
    # 登录响应带标记 → login.js:57 跳 /change-password（不用固定 sleep 猜）
    try:
        page.wait_for_url("**/change-password**", timeout=20000)
    except Exception:  # noqa: BLE001  下面用断言报告真实结果
        pass
    REPORT.check("/change-password" in page.url, f"登录后落到改密页：{page.url}")

    # 改密页自带的强制场景标记（change_password.html:36 → 成功后跳工作台）
    marker = page.evaluate("() => window.__MUST_CHANGE_PASSWORD === true")
    REPORT.check(marker is True, f"页面处于强制改密模式：{marker}")

    # 第二道保险：带 Cookie 直访业务页 → base.html meta refresh 拦回改密页
    page.goto(BASE_URL + "/documents")
    try:
        page.wait_for_url("**/change-password**", timeout=10000)
    except Exception:  # noqa: BLE001
        pass
    REPORT.check("/change-password" in page.url, f"直访 /documents 被拦回改密页：{page.url}")
    ctx.close()


@case("Y03", "浏览器层：改密页提交 → 落工作台，业务 API 恢复且控制台干净")
def y03(app):
    uid = app.state.get("gate_user_id")
    uname = app.state.get("gate_user_name")
    if not uid:
        REPORT.fail("无临时用户（依赖 Y01）")
        return

    ctx, page = anon_session(app.browser)
    _form_login(page, uname, GATE_PASSWORD)
    try:
        page.wait_for_url("**/change-password**", timeout=20000)
    except Exception:  # noqa: BLE001
        pass
    if "/change-password" not in page.url:
        REPORT.fail(f"未落到改密页，无法继续：{page.url}")
        ctx.close()
        return

    page.fill("#cp-old", GATE_PASSWORD)
    page.fill("#cp-new", NEW_PASSWORD)
    page.fill("#cp-confirm", NEW_PASSWORD)
    page.click("#cp-submit")
    # 成功后 change_password.js:43 → location.href="/"（bcrypt cost=12，给足超时）
    try:
        page.wait_for_url(re.compile(r"^" + re.escape(BASE_URL) + r"/?$"), timeout=20000)
    except Exception:  # noqa: BLE001
        pass
    REPORT.check(page.url.rstrip("/") == BASE_URL, f"改密后落工作台：{page.url}")
    # 注意不断言 ui.toast：change_password.js 先 toast 后立即 location.href="/"，
    # toast DOM 在断言前就被导航销毁——瞬时元素不可断言，改验工作台外壳（同 A02 口径）
    page.wait_for_load_state("networkidle")
    REPORT.check(page.query_selector("#app-side") is not None, "工作台外壳渲染成功（左侧栏在位）")

    # 闸门已放行：页面上下文 fetch 业务 API → 200（后端真放行，非前端障眼法）
    r = _page_fetch(page, "GET", "/api/v1/documents")
    REPORT.check(r["status"] == 200, f"改密后业务 API 恢复：{r['status']}")
    me = _page_fetch(page, "GET", "/api/v1/auth/me")
    REPORT.check(
        (me["body"] or {}).get("must_change_password") is False,
        f"改密标记已清除：{(me['body'] or {}).get('must_change_password')}",
    )
    ctx.close()


@case("Y04", "收尾：改密后旧令牌作废、新密码可登入，清理临时用户")
def y04(app):
    uid = app.state.get("gate_user_id")
    uname = app.state.get("gate_user_name")
    old_token = app.state.get("gate_token")
    if not uid:
        REPORT.fail("无临时用户（依赖 Y01）")
        return

    # 旧令牌：任一次登录/改密都会 bump token_version → 401（单点登录语义）
    stale = http("GET", "/api/v1/documents", old_token or "")
    REPORT.check(stale["status"] == 401, f"旧令牌已作废：{stale['status']}")

    # 新密码可登录且不再被闸门拦
    new_token = api_login({"username": uname, "password": NEW_PASSWORD})["access_token"]
    ok = http("GET", "/api/v1/documents", new_token)
    REPORT.check(ok["status"] == 200, f"新密码登录后业务 API 放行：{ok['status']}")

    # 清理：物理删除临时用户（未创建任何内容，门禁应放行；409 则退化为停用）
    purged = http("DELETE", f"/api/v1/users/{uid}/purge", app.admin.token)
    if purged["status"] == 200:
        REPORT.check(True, "临时用户已物理删除")
    else:
        REPORT.note(f"purge 返回 {purged['status']}，退化为停用")
        http("DELETE", f"/api/v1/users/{uid}", app.admin.token)
