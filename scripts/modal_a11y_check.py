"""弹窗键盘可达性专项检查（UX-B 回归用）。

只测 ui.js 弹窗机制本身（confirmModal / openModal 的 Esc / Tab 圈定 /
焦点落入与归还 / Promise 必定 resolve），不触碰业务数据：
通过 page.evaluate 在真实登录页面上直接调用组件，再用真实键盘事件交互。

用法：先启动 uvicorn（127.0.0.1:8000），再 python scripts/modal_a11y_check.py
"""
from __future__ import annotations

import os
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
ADMIN = {"username": "admin_demo", "password": "Demo@123456"}

RESULTS: list[tuple[str, str]] = []


def record(ok: bool, msg: str) -> None:
    RESULTS.append((ok, msg))
    print(f"  {'✅' if ok else '⚠️'} {msg}")


OPEN_CONFIRM = """
(opts) => {
    window.__result = null;
    window.__settled = false;
    window.__p = ui.confirmModal(opts);
    window.__p.then((v) => { window.__settled = true; window.__result = v; });
    return !!document.querySelector('.modal-overlay .modal');
}
"""

OPEN_PLAIN = """
(args) => {
    ui.openModal(args.html, args.opts || {});
    return !!document.querySelector('.modal-overlay .modal');
}
"""

SETTLED = "() => ({ settled: window.__settled === true, result: window.__result })"

OVERLAY_GONE = "() => !document.querySelector('.modal-overlay')"

FOCUS_IN_OVERLAY = """
() => {
    const o = document.querySelector('.modal-overlay');
    return !!o && o.contains(document.activeElement);
}
"""


def wait_closed(page) -> None:
    page.wait_for_function(OVERLAY_GONE, timeout=3000)


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="zh-CN")
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
        page.on(
            "console",
            lambda m: errors.append(f"[console.error] {m.text} @ {m.location.get('url', '')}") if m.type == "error" else None,
        )

        # ---------------- 登录 ----------------
        print("\n[0] 登录 admin_demo")
        page.goto(BASE_URL + "/login")
        page.wait_for_load_state("networkidle")
        page.fill('input[name="username"]', ADMIN["username"])
        page.fill('input[name="password"]', ADMIN["password"])
        page.click('button[type="submit"]')
        page.wait_for_url(lambda u: "/login" not in u, timeout=8000)
        page.wait_for_load_state("networkidle")
        page.goto(BASE_URL + "/admin/users")
        page.wait_for_load_state("networkidle")
        assert page.evaluate("typeof ui") == "object", "ui.js 未加载"
        record(True, f"登录并进入 {page.url.replace(BASE_URL, '')}，ui.js 可用")

        # ---------------- 1. confirmModal：初始焦点与 Tab 圈定 ----------------
        print("\n[1] confirmModal 初始焦点 / Tab 圈定")
        page.evaluate(
            OPEN_CONFIRM,
            {"title": "键盘测试", "message": "可达性演示", "danger": True},
        )
        page.wait_for_selector(".modal-overlay.show .modal")
        initial = page.evaluate(
            "() => { const el = document.activeElement;"
            " return el && el.hasAttribute('data-cancel'); }"
        )
        record(bool(initial), "初始焦点落在「取消」按钮（危险操作不默认聚焦确认）")

        in_overlay_all = True
        for _ in range(20):
            page.keyboard.press("Tab")
            if not page.evaluate(FOCUS_IN_OVERLAY):
                in_overlay_all = False
                break
        record(in_overlay_all, "连按 20 次 Tab 焦点始终圈定在弹窗内")

        page.evaluate("() => document.querySelector('.modal-close').focus()")
        page.keyboard.press("Shift+Tab")
        shifted = page.evaluate(
            "() => { const el = document.activeElement;"
            " return el && el.hasAttribute('data-confirm'); }"
        )
        record(bool(shifted), "Shift+Tab 从首元素回绕到末元素（反向循环）")
        page.keyboard.press("Escape")
        wait_closed(page)
        record(True, "本节弹窗已清理（Esc 正常关闭）")

        # ---------------- 2. Esc 关闭并 resolve(false) + 焦点归还 ----------------
        print("\n[2] Esc 关闭 / resolve / 焦点归还")
        page.evaluate(
            "() => { window.__trigger = document.querySelector('button');"
            " window.__trigger.focus(); }"
        )
        page.evaluate(
            OPEN_CONFIRM, {"title": "Esc 测试", "message": "演示", "danger": True}
        )
        page.wait_for_selector(".modal-overlay.show .modal")
        page.keyboard.press("Escape")
        wait_closed(page)
        st = page.evaluate(SETTLED)
        record(st["settled"] and st["result"] is False, "按 Esc → Promise resolve(false)")
        restored = page.evaluate(
            "() => document.activeElement === window.__trigger"
        )
        record(bool(restored), "关闭后焦点归还到触发元素")

        # ---------------- 3. ✕ / 遮罩 / 确认按钮三条路径 ----------------
        print("\n[3] ✕ / 遮罩 / 确认按钮")
        page.evaluate(
            "() => { window.__trigger = document.querySelector('button');"
            " window.__trigger.focus(); }"
        )
        page.evaluate(OPEN_CONFIRM, {"title": "✕ 测试", "message": "演示"})
        page.wait_for_selector(".modal-overlay.show .modal")
        page.click(".modal-close")
        wait_closed(page)
        st = page.evaluate(SETTLED)
        record(st["settled"] and st["result"] is False, "点 ✕ → resolve(false)（修复挂起 bug）")
        restored = page.evaluate("() => document.activeElement === window.__trigger")
        record(bool(restored), "✕ 关闭后焦点同样归还")

        page.evaluate(OPEN_CONFIRM, {"title": "遮罩测试", "message": "演示"})
        page.wait_for_selector(".modal-overlay.show .modal")
        page.mouse.click(15, 450)
        wait_closed(page)
        st = page.evaluate(SETTLED)
        record(st["settled"] and st["result"] is False, "点遮罩 → resolve(false)（修复挂起 bug）")

        page.evaluate(OPEN_CONFIRM, {"title": "确认测试", "message": "演示"})
        page.wait_for_selector(".modal-overlay.show .modal")
        page.click("[data-confirm]")
        wait_closed(page)
        st = page.evaluate(SETTLED)
        record(st["settled"] and st["result"] is True, "点「确认」→ resolve(true)")

        # ---------------- 4. 输入型确认弹窗 ----------------
        print("\n[4] 输入型确认弹窗")
        page.evaluate(
            OPEN_CONFIRM,
            {"title": "原因", "message": "请填写原因", "inputLabel": "修改原因",
             "inputRequired": True},
        )
        page.wait_for_selector(".modal-overlay.show .modal")
        in_input = page.evaluate(
            "() => document.activeElement && document.activeElement.id === 'confirm-input'"
        )
        record(bool(in_input), "有输入框时初始焦点落在输入框")
        page.click("[data-confirm]")
        still_open = page.evaluate("() => !!document.querySelector('.modal-overlay')")
        record(bool(still_open), "必填校验仍生效：空值点确认不关闭")
        page.keyboard.press("Escape")
        wait_closed(page)
        st = page.evaluate(SETTLED)
        record(st["settled"] and st["result"] is False, "输入中途 Esc → resolve(false)")

        # ---------------- 5. 普通表单弹窗（openModal） ----------------
        print("\n[5] openModal 表单弹窗")
        page.evaluate(
            OPEN_PLAIN,
            {"html": '<input id="a11y-first-field" type="text">',
             "opts": {"title": "表单弹窗"}},
        )
        page.wait_for_selector(".modal-overlay.show .modal")
        first_field = page.evaluate(
            "() => document.activeElement && document.activeElement.id === 'a11y-first-field'"
        )
        record(bool(first_field), "默认初始焦点 = 正文第一个可聚焦元素")
        page.keyboard.press("Escape")
        wait_closed(page)
        record(True, "openModal 弹窗同样支持 Esc 关闭")

        # ---------------- 6. closable:false 强制弹窗 ----------------
        print("\n[6] closable:false")
        page.evaluate(
            OPEN_PLAIN,
            {"html": "<div>必须处理</div>", "opts": {"title": "强制弹窗", "closable": False}},
        )
        page.wait_for_selector(".modal-overlay.show .modal")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        still_open = page.evaluate("() => !!document.querySelector('.modal-overlay')")
        record(bool(still_open), "closable:false 时 Esc 不关闭")
        page.evaluate("() => ui.closeModal()")
        wait_closed(page)

        # ---------------- 汇总 ----------------
        print("\n" + "=" * 56)
        ok = sum(1 for s, _ in RESULTS if s)
        print(f"结果: {ok}/{len(RESULTS)} 项通过")
        if errors:
            print("页面错误:")
            for e in errors:
                print("  " + e)
        browser.close()

    if ok == len(RESULTS) and not errors:
        print("✅ 弹窗键盘可达性全部通过")
        raise SystemExit(0)
    print("⚠️ 存在未通过项")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
