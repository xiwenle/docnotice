#!/usr/bin/env python3
"""前端 P1 修复的定向浏览器验证（2026-09-29）。

后端 505 项测试**不覆盖 JS**（零 DOM 断言），所以本轮对
`api.js` / `login.js` / `ui.js` 的改动必须另做真实浏览器验证。

验证对象（两条 P1）：
  P1-A  login：从 `/login?next=…` 进入、输错密码 → 必须**就地显示错误**，
        不能被再次重定向（修复前会被踢到 /login?next=%2Flogin%3Fnext%3D…，提示一闪而过）
  P1-B  confirmModal：message 里的 <strong>/<br> 必须**渲染成真标签**，
        而其它标签（script/img/带属性的标签）必须**保持转义**

用法（先启动被测实例）：
    DATA_DIR=<临时目录>/fms_check_data PORT=8010 python -m uvicorn app.main:app --port 8010 &
    python scripts/frontend_p1_check.py
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8010"
ADMIN = ("admin_demo", "Demo@123456")

fails: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label + (("  ← " + detail) if detail else ""))
    if not cond:
        fails.append(label + (" | " + detail if detail else ""))


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True,
                              args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = b.new_context(viewport={"width": 1280, "height": 900})
        pg = ctx.new_page()
        errs: list[str] = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))

        # ---------------- P1-A：登录失败不得自跳转 ----------------
        print("\n[P1-A] 登录失败就地提示（修复前会被踢到 /login?next=…）")
        pg.goto(f"{BASE}/login?next=%2Fdocuments", wait_until="load")
        check("起始 URL 带 next", "/login?next=" in pg.url, pg.url)
        pg.fill("#username", ADMIN[0])
        pg.fill("#password", "definitely-wrong-password")
        pg.click("#login-submit")
        # 等错误框出现（而不是等死时间）
        try:
            pg.wait_for_selector("#login-error:not(.hidden)", timeout=8000)
            shown = True
        except Exception:  # noqa: BLE001  超时即视为未显示，正是本用例要断言的负路径
            shown = False
        check("错误提示已显示", shown)
        check("错误文案是「用户名或密码错误」",
              "用户名或密码错误" in (pg.text_content("#login-error-text") or ""),
              (pg.text_content("#login-error-text") or "").strip()[:40])
        check("URL 未被再次重定向（仍是单层 next）",
              pg.url.count("next=") == 1, pg.url)
        check("没有出现嵌套 next", "%2Flogin" not in pg.url.upper(), pg.url)

        # 正确密码仍然能登录并落到 next 指定页
        pg.fill("#password", ADMIN[1])
        pg.click("#login-submit")
        pg.wait_for_url("**/documents**", timeout=15000)
        check("错误后改用正确密码可正常登录并跳到 next 目标", "/documents" in pg.url, pg.url)

        # ---------------- P1-B：confirmModal 白名单渲染 ----------------
        print("\n[P1-B] confirmModal：白名单标签渲染、其余转义")
        result = pg.evaluate("""async () => {
          const cases = [
            "即将<strong>物理删除</strong>「报告.pdf」的文件副本",
            "A<br><strong>此操作不可直接撤销</strong>（可再恢复其它备份）",
            "<script>alert(1)</script>",
            "<img src=x onerror=alert(1)>",
            "<strong onclick=\\"x()\\">带属性</strong>",
            "用户输入里真写的 &lt;strong&gt; 字样",
          ];
          const out = [];
          const sleep = ms => new Promise(r => setTimeout(r, ms));
          for (const msg of cases) {
            ui.confirmModal({ title: "t", message: msg, danger: true });
            await sleep(40);
            // 必须取**最后一个** .modal-body：closeModal 是「先摘 show、200ms 后移除节点」，
            // 若不等它移除，document.querySelector 命中的是上一轮的残骸 → 读到上一条内容
            const all = document.querySelectorAll(".modal-body");
            const body = all.length ? all[all.length - 1] : null;
            out.push({
              input: msg,
              html: body ? body.innerHTML : null,
              text: body ? body.textContent : null,
              strong: body ? body.querySelectorAll("strong").length : -1,
              br: body ? body.querySelectorAll("br").length : -1,
              script: body ? body.querySelectorAll("script").length : -1,
              img: body ? body.querySelectorAll("img").length : -1,
              attrStrong: body ? body.querySelectorAll("strong[onclick]").length : -1,
            });
            // 关掉（走 Esc 路径，顺带验证 onRequestClose）；等节点真正被移除再进下一条
            document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
            await sleep(260);
          }
          return out;
        }""")

        c0, c1, c2, c3, c4, c5 = result
        check("① 强调标签渲染为真 <strong>", c0["strong"] == 1, f"strong={c0['strong']}")
        check("① 文字里不含字面标签", "<strong>" not in (c0["text"] or ""), (c0["text"] or "")[:40])
        check("② <br> + <strong> 都渲染", c1["br"] == 1 and c1["strong"] == 1,
              f"br={c1['br']} strong={c1['strong']}")
        check("③ <script> 被转义（无 script 节点）", c2["script"] == 0, f"script={c2['script']}")
        check("③ 文字里能看到转义后的原文", "<script>" in (c2["text"] or ""), (c2["text"] or "")[:40])
        check("④ <img onerror> 被转义（无 img 节点）", c3["img"] == 0, f"img={c3['img']}")
        check("⑤ 带属性的标签不放行（无 strong[onclick]）",
              c4["attrStrong"] == 0, f"attrStrong={c4['attrStrong']}")
        check("⑤ 属性标签以转义形式可见", "onclick" in (c4["text"] or ""), (c4["text"] or "")[:50])
        # 注意判据：escapeHtml 先把 & 转成 &amp;，所以 innerHTML 里是 `&amp;lt;strong&amp;gt;`，
        # 浏览器解析后的 textContent 才是字面的 `&lt;strong&gt;`。
        # 判据：textContent 里应当出现**字面的** `&lt;strong&gt;`（含 & 与 ; 的原始字符），
        # 且**没有**真的 <strong> 元素 —— 说明「用户输入里的尖括号」没有被当成标签放行。
        check("⑥ 用户输入里的字面 &lt;strong&gt; 不被回填",
              c5["strong"] == 0 and "&lt;strong&gt;" in (c5["text"] or ""),
              f"strong={c5['strong']} text={(c5['text'] or '')[:44]}")

        # ---------------- 顺带：真实页面的确认弹窗（回收站） ----------------
        print("\n[附带] 真实调用点（若回收站有内容则验回收站永久删除弹窗）")
        pg.goto(f"{BASE}/admin/recycle-bin", wait_until="load")
        pg.wait_for_timeout(1200)
        rows = pg.query_selector_all("tr[data-id]")
        if rows:
            pg.evaluate("""() => {
              const b = document.querySelector('[data-action="purge"]');
              if (b) b.click();
            }""")
            pg.wait_for_timeout(400)
            html = pg.evaluate("() => { const b=document.querySelector('.modal-body'); return b?b.innerHTML:null; }")
            check("回收站永久删除弹窗渲染正常（含真 <strong>）",
                  bool(html) and "<strong>" in html, (html or "")[:70])
        else:
            print("  SKIP  回收站为空，跳过（不计入失败）")

        # 预期噪声：401（本轮故意输错密码）、403（越权探测）—— 都属用例自身制造的 4xx，
        # 不是产品缺陷。其余任何 console error 都算失败。
        unexpected = [e for e in errs if "401" not in e and "403" not in e]
        check("除预期 4xx 外控制台零 error", len(unexpected) == 0, "; ".join(unexpected[:3]))
        b.close()

    print("\n" + "=" * 60)
    if fails:
        print("失败 %d 项：" % len(fails))
        for f in fails:
            print("  -", f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
