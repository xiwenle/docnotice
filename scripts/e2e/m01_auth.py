"""M01 认证与会话：登录表单、失败分支、开放重定向、登出、改密、临时用户清理。"""
from __future__ import annotations

import re
import time

from harness import ADMIN, BASE_URL, REPORT, SHOT_DIR, anon_session, api_login, case, http


@case("A01", "匿名访问受保护页面 → 跳登录页并带 next 回跳")
def a01(app):
    ctx, page = anon_session(app.browser)
    page.goto(BASE_URL + "/documents")
    page.wait_for_load_state("networkidle")
    REPORT.check(page.url.startswith(BASE_URL + "/login"), f"跳到登录页：{page.url}")
    # 前端侧 api.js:redirectToLogin() 会带 next 且已做开放重定向防护；服务端 SSR 守卫未携带。
    # 表现为：会话过期后从 deep link 进入 → 登录后落到首页，回不到原目标（待产品确认，级别 P2）
    if "next=" not in page.url:
        REPORT.note(f"SSR 重定向未携带 next 回跳参数（{page.url}）→ 深链接登录后回不到原页面")
    else:
        REPORT.check(True, f"携带回跳参数：{page.url}")
    ctx.close()


@case("A02", "表单登录成功（真实点击提交）")
def a02(app):
    ctx, page = anon_session(app.browser)
    errs: list[str] = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(BASE_URL + "/login")
    page.fill("#username", ADMIN["username"])
    page.fill("#password", ADMIN["password"])
    page.click("#login-submit")
    # 不要用固定 sleep：bcrypt(cost=12) 在负载下可能 >1.2s，等待「真的离开登录页」
    try:
        page.wait_for_url(re.compile(rf"^{re.escape(BASE_URL)}/(\?.*)?$"), timeout=20000)
    except Exception:  # noqa: BLE001  下面用断言报告真实结果
        pass
    page.wait_for_load_state("networkidle")
    REPORT.check(page.url.rstrip("/") == BASE_URL, f"落到工作台：{page.url}")
    REPORT.check(page.query_selector("#app-side") is not None, "左侧栏渲染成功")
    REPORT.check(page.query_selector("#login-form") is None, "登录表单已消失")
    REPORT.check(not errs, f"登录过程无 JS 异常：{errs[:2]}")
    page.screenshot(path=str(SHOT_DIR / "A02_logged_in.png"))
    ctx.close()
    app.resync_all()  # 别的 context 登录过同一账号 → 主会话令牌已失效，需续签


@case("A03", "登录失败：密码错误 → 明确提示且不跳转")
def a03(app):
    ctx, page = anon_session(app.browser)
    page.goto(BASE_URL + "/login")
    page.fill("#username", ADMIN["username"])
    page.fill("#password", "wrong-password-xxx")
    page.click("#login-submit")
    try:
        page.wait_for_selector("#login-error", state="visible", timeout=20000)
    except Exception:  # noqa: BLE001
        pass
    box = page.query_selector("#login-error")
    visible = box is not None and "hidden" not in (box.get_attribute("class") or "")
    REPORT.check(visible, "显示错误横幅")
    txt = box.inner_text() if box else ""
    REPORT.check("用户名或密码错误" in txt, f"文案正确：{txt.strip()[:40]}")
    REPORT.check("/login" in page.url, f"停留在登录页：{page.url}")
    ctx.close()


@case("A04", "登录表单：空值本地校验（不发起请求）")
def a04(app):
    ctx, page = anon_session(app.browser)
    calls: list[str] = []
    page.on("request", lambda r: calls.append(r.url) if "/auth/login" in r.url else None)
    page.goto(BASE_URL + "/login")
    page.click("#login-submit")
    page.wait_for_timeout(700)
    REPORT.check(not calls, f"空表单未发起登录请求（{len(calls)} 次）")
    cls = page.evaluate(
        "() => document.querySelector('#username').closest('.form-group').className")
    REPORT.check("has-error" in cls, f"用户名分组标红：{cls}")
    ctx.close()


@case("A05", "开放重定向防护：next=//evil.example 不外跳")
def a05(app):
    from urllib.parse import quote

    ctx, page = anon_session(app.browser)
    page.goto(f"{BASE_URL}/login?next={quote('//evil.example/steal', safe='')}")
    page.fill("#username", ADMIN["username"])
    page.fill("#password", ADMIN["password"])
    page.click("#login-submit")
    page.wait_for_load_state("networkidle")
    from urllib.parse import urlparse

    host = urlparse(page.url).netloc
    REPORT.check(page.url.startswith(BASE_URL), f"未跳外站：{page.url}")
    # 注意：next 参数原样留在 URL 里不算外跳，只有「host 变成 evil.example」才是
    REPORT.check("evil.example" not in host, f"未被跳转到外站（host={host}）")
    ctx.close()
    app.resync_all()


@case("A06", "登出 → 会话失效，受保护页重新拦截")
def a06(app):
    s = app.admin
    s.goto("/")
    s.page.click("#btn-logout")
    try:
        s.page.wait_for_url("**/login**", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    s.page.wait_for_load_state("networkidle")
    REPORT.check("/login" in s.page.url, f"登出回登录页：{s.page.url}")
    s.goto("/documents", ensure_session=False)  # 关键：不得自动续签，否则测不出真实登出效果
    REPORT.check("/login" in s.page.url, f"登出后访问 /documents 拦截：{s.page.url}")
    # 恢复会话（后续用例依赖）
    s.token = api_login(ADMIN)["access_token"]
    s.ctx.add_cookies([{"name": "access_token", "value": s.token, "url": BASE_URL}])
    s.goto("/")
    REPORT.check(s.page.url.rstrip("/") == BASE_URL, "会话已恢复")


@case("A07", "篡改令牌 → 拦截回登录")
def a07(app):
    ctx, page = anon_session(app.browser)
    page.goto(BASE_URL + "/login")
    ctx.add_cookies([{"name": "access_token", "value": "tampered.token.value", "url": BASE_URL}])
    page.goto(BASE_URL + "/")
    page.wait_for_load_state("networkidle")
    REPORT.check("/login" in page.url, f"无效令牌被拦截：{page.url}")
    ctx.close()


@case("A08", "修改密码：旧密码/弱密码/不一致均拒绝，正确时成功")
def a08(app):
    uname = f"e2e_pwd_{int(time.time())}"
    pwd = "Demo@123456"
    r = http("POST", "/api/v1/users", app.admin.token,
             {"username": uname, "password": pwd, "display_name": "临时改密用户", "role": "user"})
    REPORT.check(r["status"] in (200, 201), f"创建临时用户：{r['status']}")
    uid = (r["body"] or {}).get("id")
    app.state["tmp_user_id"] = uid
    app.state["tmp_user_name"] = uname
    if not uid:
        REPORT.fail("未取到临时用户 id，后续分支跳过")
        return

    ctx = app.browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN")
    t = api_login({"username": uname, "password": pwd})["access_token"]
    ctx.add_cookies([{"name": "access_token", "value": t, "url": BASE_URL}])
    page = ctx.new_page()
    page.goto(BASE_URL + "/change-password")
    page.wait_for_load_state("networkidle")

    def ensure_page():
        if "/change-password" not in page.url:
            page.goto(BASE_URL + "/change-password")
            page.wait_for_load_state("networkidle")

    def errbox(wait: bool = True):
        """读改密页错误框。wait=True 时等它真的可见（不许用固定 sleep 猜）。"""
        if wait:
            try:
                page.wait_for_selector("#cp-error", state="visible", timeout=20000)
            except Exception:  # noqa: BLE001
                pass
        try:
            e = page.query_selector("#cp-error")
            vis = e is not None and "hidden" not in (e.get_attribute("class") or "")
            t = page.query_selector("#cp-error-text")
            return vis, (t.inner_text().strip() if t else "")
        except Exception:  # noqa: BLE001  页面导航中，忽略本轮
            return False, ""

    def submit(old, new, confirm, expect_error: bool = True, expect_success: bool = False):
        """expect_error=True 时等错误框出现（服务端往返）；expect_success=True 时
        等真的离开改密页（成功分支 location.href="/"）。

        禁止用固定 sleep 猜异步完成（docs/E2E_AUDIT_2026-09-14.md §6）：
        bcrypt(cost=12) 改密实测 ~730ms，固定 600ms 在负载下必然竞态，
        会让「新密码可登录」用例假失败——这正是 A08 ④ 曾经的偶发来源。
        """
        ensure_page()
        page.fill("#cp-old", old)
        page.fill("#cp-new", new)
        page.fill("#cp-confirm", confirm)
        page.click("#cp-submit")
        if expect_error:
            # 提交瞬间 JS 会先把框隐藏，服务端返回后再显示 → 等它再次可见
            try:
                page.wait_for_selector("#cp-error.hidden", state="attached", timeout=2000)
            except Exception:  # noqa: BLE001
                pass
            errbox(wait=True)
        elif expect_success:
            # 条件等待：改密成功后前端跳首页（足够长以覆盖 bcrypt 慢哈希）
            try:
                page.wait_for_url(lambda u: "/change-password" not in u, timeout=20000)
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_load_state("networkidle")
        else:
            # 前端拦截分支（不一致）不发起请求，只需等 DOM 标红
            page.wait_for_selector("#cp-confirm.has-error, .form-group.has-error", timeout=5000)

    submit("wrong-old-pwd", "Another@2026", "Another@2026")
    vis, msg = errbox(wait=False)
    if "/change-password" not in page.url:
        REPORT.fail(
            "① 输错原密码后未在本页提示「原密码不正确」，而是被当作会话失效踢到登录页"
            f"（{page.url}）——api.js 对全部 401 统一 redirectToLogin，未区分 UNAUTHORIZED 语义")
    else:
        REPORT.check(vis and msg, f"① 旧密码错误被拒：{vis} / {msg[:40]}")

    submit(pwd, "123", "123")
    vis, msg = errbox(wait=False)
    REPORT.check(vis and msg, f"② 弱密码被拒：{vis} / {msg[:40]}")

    submit(pwd, "Strong@2026a", "Strong@2026b", expect_error=False)
    cls = page.evaluate(
        "() => {const g = document.querySelector('#cp-confirm').closest('.form-group');"
        " return g ? g.className : '';}")
    REPORT.check("has-error" in cls, f"③ 两次不一致被前端拦下并标红：{cls}")

    newpwd = "Renewed@2026"
    submit(pwd, newpwd, newpwd, expect_error=False, expect_success=True)
    login = http("POST", "/api/v1/auth/login", "", {"username": uname, "password": newpwd})
    REPORT.check(login["status"] == 200, f"④ 新密码可登录：{login['status']}")
    old = http("POST", "/api/v1/auth/login", "", {"username": uname, "password": pwd})
    REPORT.check(old["status"] != 200, f"⑤ 旧密码已失效：{old['status']}")
    app.state["tmp_user_newpwd"] = newpwd
    ctx.close()


@case("A09", "清理临时用户（purge）")
def a09(app):
    uid = app.state.get("tmp_user_id")
    if not uid:
        REPORT.check(False, "无临时用户可清理")
        return
    r = http("DELETE", f"/api/v1/users/{uid}/purge", app.admin.token)
    REPORT.check(r["status"] in (200, 204), f"临时用户已清理：{r['status']} {str(r['body'])[:60]}")
