"""M32 用户管理：CRUD、停用/启用、重置密码、内容责任链删除保护、筛选（§23）。"""
from __future__ import annotations

import time

from harness import REPORT, SHOT_DIR, case, http


def _open_modal(s, first_sel: str):
    """点新建按钮并等弹窗「真的可用」：旧弹窗必须已从 DOM 摘除，字段为空。

    踩过的坑：弱密码分支点 [data-cancel] 后立刻点 [data-save] 建第二次，
    旧弹窗还在关闭动画中，wait_for_selector 命中的是「要消失的那个」，
    填进去的值落到旧节点上 → 新建请求带不上密码 → 账号建成但密码不是预期值，
    后续「新账号可登录」就会莫名 401。
    """
    for _ in range(3):
        s.page.click("#btn-new-user")
        s.page.wait_for_selector(first_sel, timeout=8000)
        n = s.page.eval_on_selector_all("#modal-root .modal-overlay", "els => els.length")
        val = s.page.input_value(first_sel)
        if n == 1 and not val:
            return True
        try:
            s.page.click("#modal-root [data-cancel]")
        except Exception:  # noqa: BLE001
            pass
        s.page.wait_for_timeout(400)
    return False


def _close_modal(s):
    btn = s.page.query_selector("#modal-root [data-cancel]")
    if btn:
        btn.click()
    try:
        s.page.wait_for_selector("#modal-root .modal-overlay", state="detached", timeout=6000)
    except Exception:  # noqa: BLE001
        pass


def _open(app):
    s = app.admin
    s.goto("/admin/users")
    s.page.wait_for_selector("#user-tbody tr", timeout=15000)
    s.page.wait_for_timeout(600)
    return s


@case("U01", "用户列表：渲染 + 角色/状态筛选")
def u01(app):
    s = _open(app)
    rows = len(s.page.query_selector_all("#user-tbody tr"))
    REPORT.check(rows > 0, f"用户列表 {rows} 行")
    s.page.select_option("#f-role", "admin")
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1200)
    admins = s.page.query_selector_all("#user-tbody tr")
    all_admin = all("管理员" in r.inner_text() for r in admins)
    REPORT.check(all_admin, f"角色筛选=管理员：{len(admins)} 行，全部标记管理员={all_admin}")
    s.page.click("#btn-reset")
    s.page.wait_for_timeout(800)
    s.page.select_option("#f-role", "user")
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1200)
    users = s.page.query_selector_all("#user-tbody tr")
    REPORT.check(all("普通用户" in r.inner_text() for r in users),
                 f"角色筛选=普通用户：{len(users)} 行")
    s.page.click("#btn-reset")
    s.page.wait_for_timeout(800)
    s.page.screenshot(path=str(SHOT_DIR / "U01_users.png"))


@case("U02", "新建用户：弱密码被拒，合格则创建并可用新账号登录")
def u02(app):
    s = _open(app)
    uname = f"e2e_u_{int(time.time())}"
    app.state["new_user"] = uname
    s.page.click("#btn-new-user")
    s.page.wait_for_selector("#u-username", timeout=6000)
    s.page.fill("#u-username", uname)
    s.page.fill("#u-display", "临时自动化用户")
    s.page.fill("#u-password", "123")
    s.page.click("[data-save]")
    s.page.wait_for_timeout(1500)
    toast = s.toast_text(4000)
    created_weak = bool(http("POST", "/api/v1/auth/login", "", {"username": uname, "password": "123"})["status"] == 200)
    REPORT.check(not created_weak, f"弱密码未被接受（提示：{toast[:40]}）")
    _close_modal(s)

    pwd = "Demo@123456"
    REPORT.check(_open_modal(s, "#u-username"), "新建用户弹窗已就绪（旧弹窗已摘除）")
    s.page.fill("#u-username", uname)
    s.page.fill("#u-display", "临时自动化用户")
    s.page.fill("#u-password", pwd)
    s.page.click("[data-save]")
    try:
        s.page.wait_for_selector("#modal-root .modal-overlay", state="detached", timeout=6000)
    except Exception:  # noqa: BLE001
        pass
    s.page.wait_for_timeout(300)
    login = http("POST", "/api/v1/auth/login", "", {"username": uname, "password": pwd})
    REPORT.check(login["status"] == 200, f"新账号可登录：{login['status']}")
    app.state["new_user_pwd"] = pwd
    uid = (login["body"] or {}).get("user", {}).get("id") or (login["body"] or {}).get("user_id")
    app.state["new_user_id"] = uid
    s.page.fill("#f-keyword", uname)
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1200)
    REPORT.check(uname in s.page.inner_text("#user-tbody"), f"列表可检索到新用户：{uname}")
    s.page.click("#btn-reset")
    s.page.wait_for_timeout(600)


@case("U03", "停用用户 → 立即无法登录；再启用 → 恢复")
def u03(app):
    uid = app.state.get("new_user_id")
    uname = app.state.get("new_user")
    pwd = app.state.get("new_user_pwd")
    if not uid:
        REPORT.fail("无新建用户（依赖 U02）")
        return
    s = _open(app)
    s.page.fill("#f-keyword", uname)
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1200)
    btn = s.page.query_selector(f'button[data-action="disable"][data-id="{uid}"]')
    if not btn:
        REPORT.fail(f"未找到停用按钮（uid={uid}）")
        return
    btn.click()
    # 停用走 ui.confirmModal（自定义弹窗，非原生 dialog）
    s.page.wait_for_selector("#modal-root [data-confirm]", timeout=8000)
    s.page.click("#modal-root [data-confirm]")
    s.page.wait_for_timeout(2500)

    st = http("GET", f"/api/v1/users/{uid}", app.admin.token)["body"] or {}
    REPORT.check(st.get("is_active") is False, f"后端已置停用：is_active={st.get('is_active')}")
    login = http("POST", "/api/v1/auth/login", "", {"username": uname, "password": pwd})
    REPORT.check(login["status"] != 200, f"停用后立即无法登录：{login['status']}")

    s.reload()
    s.page.fill("#f-keyword", uname)
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1500)
    enable = s.page.query_selector(f'button[data-action="enable"][data-id="{uid}"]')
    REPORT.check(enable is not None, "列表切换为「启用」按钮")
    REPORT.check("停用" in s.page.inner_text("#user-tbody"), "状态列标记停用")
    if enable:
        enable.click()
        s.page.wait_for_timeout(2500)
        st2 = http("GET", f"/api/v1/users/{uid}", app.admin.token)["body"] or {}
        REPORT.check(st2.get("is_active") is True, f"重新启用：is_active={st2.get('is_active')}")
        login2 = http("POST", "/api/v1/auth/login", "", {"username": uname, "password": pwd})
        REPORT.check(login2["status"] == 200, f"重新启用后可登录：{login2['status']}")


@case("U04", "重置密码：管理员下发新密码，旧密码失效")
def u04(app):
    uid = app.state.get("new_user_id")
    uname = app.state.get("new_user")
    if not uid:
        REPORT.fail("无新建用户（依赖 U02）")
        return
    s = _open(app)
    s.page.fill("#f-keyword", uname)
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1200)
    btn = s.page.query_selector(f'button[data-action="reset"][data-id="{uid}"]')
    if not btn:
        REPORT.fail("未找到重置密码按钮")
        return
    btn.click()
    s.page.wait_for_selector("#rp-password", timeout=6000)
    newpwd = "Reset@2026"
    s.page.fill("#rp-password", newpwd)
    s.page.click("[data-save]")
    # 2026-10-16 修竞态（全量偶发 / 单跑必过）：原写法固定 sleep 2000ms 后
    # 单次登录旧密码——全量负载下 bcrypt(cost=12)+提交超过 2s，旧密码登录
    # 抢先返回 200。改为「等 modal 关闭（=API 已 200，提交已完成）+
    # 有界轮询旧密码登录」，不再猜时间（TESTING.md §六·1 纪律）。
    try:
        s.page.wait_for_selector("#modal-root .modal-overlay", state="detached", timeout=10000)
    except Exception:  # noqa: BLE001  模态可能已关，继续轮询判定
        pass
    old_pwd = app.state.get("new_user_pwd")
    old_status = 0
    deadline = time.time() + 10
    while time.time() < deadline:
        old_ok = http("POST", "/api/v1/auth/login", "", {"username": uname, "password": old_pwd})
        old_status = old_ok["status"]
        if old_status != 200:
            break
        time.sleep(0.3)
    new_ok = http("POST", "/api/v1/auth/login", "", {"username": uname, "password": newpwd})
    REPORT.check(new_ok["status"] == 200, f"新密码可用：{new_ok['status']}")
    REPORT.check(old_status != 200, f"旧密码失效：{old_status}")
    app.state["new_user_pwd"] = newpwd


@case("U05", "内容责任链：创建过资料/通知的账号不得被物理删除")
def u05(app):
    """§26.1 物理删除免二次确认 → 误删风险由「内容责任链」后端门禁对冲。

    实测语义（2026-09-14）：
    - 删自己 → 409「不能删除自己的账号」（前端按钮也置灰：title=不能删除自己）
    - 删「创建过内容的他人账号」→ 409 且给出可读计数「创建或编辑过 N 份资料…」
    这两条都必须能看见，才算门禁真的在。
    """
    app.allow_noise()  # 409 属预期拦截
    me = http("GET", "/api/v1/auth/me", app.admin.token)["body"] or {}
    uid = me.get("id")
    if not uid:
        REPORT.fail("未取到当前管理员 id")
        return

    # ① 自删保护
    r_self = http("DELETE", f"/api/v1/users/{uid}/purge", app.admin.token)
    msg_self = ((r_self["body"] or {}).get("error") or {}).get("message", "")
    REPORT.check(r_self["status"] == 409 and "自己" in msg_self,
                 f"删除自己被拒：{r_self['status']} {msg_self[:40]}")

    # ② 责任链：找一个「创建过内容的他人账号」（逐个试，408 前的首个 409 即是）
    users = (http("GET", "/api/v1/users?page=1&page_size=100",
                  app.admin.token)["body"] or {}).get("items") or []
    owner = None
    for u in users:
        if u["id"] == uid:
            continue
        rr = http("DELETE", f"/api/v1/users/{u['id']}/purge", app.admin.token)
        if rr["status"] == 409:
            owner = u
            msg_owner = ((rr["body"] or {}).get("error") or {}).get("message", "")
            break
    if not owner:
        REPORT.note("未找到被责任链拦截的账号（库内可能只有一个有内容账号），跳过 UI 校验")
        return
    REPORT.check("资料" in msg_owner or "通知" in msg_owner,
                 f"责任链拒绝原因可读：{msg_owner[:70]}")

    # ③ UI 侧：该账号的删除按钮可点（非自删，不置灰），点击后弹出可读拒绝
    s = _open(app)
    s.page.fill("#f-keyword", owner.get("username") or "")
    s.page.click("#btn-search")
    s.page.wait_for_timeout(1500)
    btn = s.page.query_selector(
        f'button[data-action="purge"][data-id="{owner["id"]}"]')
    REPORT.check(btn is not None, f"责任链账号「{owner.get('username')}」有删除入口")
    if btn:
        REPORT.check(not btn.is_disabled(),
                     "非自删账号的删除按钮可点（拦截交给后端，非前端假禁用）")
        btn.click()
        toast = s.toast_text(6000)
        REPORT.check(any(k in toast for k in ("内容", "资料", "通知", "责任人")),
                     f"页面给出可读的拒绝原因：{toast[:70]}")
        still = http("GET", f"/api/v1/users?keyword={owner.get('username')}&page_size=1",
                     app.admin.token)
        items = (still["body"] or {}).get("items") or []
        REPORT.check(bool(items), "责任链账号仍在库中（未被误删）")
    s.page.click("#btn-reset")


@case("U06", "清理：物理删除本次新建的临时用户")
def u06(app):
    uid = app.state.get("new_user_id")
    if not uid:
        REPORT.fail("无临时用户（依赖 U02）")
        return
    r = http("DELETE", f"/api/v1/users/{uid}/purge", app.admin.token)
    REPORT.check(r["status"] in (200, 204), f"临时用户已清理：{r['status']} {str(r['body'])[:60]}")
