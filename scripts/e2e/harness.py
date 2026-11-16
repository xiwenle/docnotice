"""E2E 测试骨架：用例注册、浏览器会话、断言原语、报告输出。

设计原则（对齐 项目规范 §30/§32/§33）：
- 全部走**真实浏览器**（Playwright + Chrome headless），UI 操作由脚本驱动，不 mock
- 用例失败**不中断**整轮，全部跑完后统一汇总
- 失败自动截图到 screenshots/e2e/
- 每个用例自带控制台 error 侦听，交互类用例默认要求「零 console error」
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8010")
SHOT_DIR = Path(__file__).resolve().parents[2] / "screenshots" / "e2e"
SHOT_DIR.mkdir(parents=True, exist_ok=True)

ADMIN = {"username": os.environ.get("E2E_ADMIN", "admin_demo"),
         "password": os.environ.get("E2E_ADMIN_PWD", "Demo@123456")}
NORMAL = {"username": os.environ.get("E2E_USER", "user_rnd_1"),
          "password": os.environ.get("E2E_USER_PWD", "Demo@123456")}


# --------------------------------------------------------------------------
# 结果收集
# --------------------------------------------------------------------------
class Report:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.current: dict | None = None

    def start(self, code: str, title: str) -> None:
        self.current = {"code": code, "title": title, "status": "PASS",
                        "msgs": [], "errors": [], "shots": [], "t0": time.time()}
        print(f"\n▶ {code} {title}")

    def check(self, ok: bool, msg: str) -> bool:
        if self.current is None:
            raise RuntimeError("check() 必须在用例内调用")
        mark = "PASS" if ok else "FAIL"
        print(f"   [{mark}] {msg}")
        self.current["msgs"].append((ok, msg))
        if not ok:
            self.current["status"] = "FAIL"
        return ok

    def fail(self, msg: str) -> None:
        self.check(False, msg)

    def note(self, msg: str) -> None:
        """信息性记录：不参与 PASS/FAIL 判定（用于「已知行为/待产品确认」）。"""
        if self.current is None:
            return
        print(f"   [NOTE] {msg}")
        self.current["msgs"].append((True, f"{msg} 〔非判定项〕"))

    def error(self, exc: BaseException) -> None:
        if self.current is None:
            return
        self.current["status"] = "ERROR"
        self.current["errors"].append("".join(traceback.format_exception_only(type(exc), exc)).strip())

    def shot(self, page, name: str) -> str:
        path = SHOT_DIR / name
        try:
            page.screenshot(path=str(path), full_page=False, animations="disabled")
        except Exception:  # noqa: BLE001
            return ""
        self.current["shots"].append(str(path))
        return str(path)

    def end(self) -> None:
        if self.current is None:
            return
        self.current["ms"] = int((time.time() - self.current["t0"]) * 1000)
        self.rows.append(self.current)
        self.current = None

    # ---- 汇总 ----
    def summary(self) -> tuple[int, int, int]:
        print("\n" + "=" * 78)
        fail = [r for r in self.rows if r["status"] == "FAIL"]
        err = [r for r in self.rows if r["status"] == "ERROR"]
        ok_n = len(self.rows) - len(fail) - len(err)
        for r in self.rows:
            if r["status"] != "PASS":
                print(f"  {r['status']:5} {r['code']} {r['title']}")
                for ok, m in r["msgs"]:
                    if not ok:
                        print(f"         × {m}")
                for e in r["errors"]:
                    print(f"         ! {e}")
        print("=" * 78)
        print(f"用例 {len(self.rows)}：PASS {ok_n} / FAIL {len(fail)} / ERROR {len(err)}")
        return ok_n, len(fail), len(err)


REPORT = Report()

# 用例注册表：[(code, title, fn)]
CASES: list[tuple[str, str, object]] = []


def case(code: str, title: str):
    """用例装饰器：fn(app) -> None，app 为 App 实例。"""
    def deco(fn):
        CASES.append((code, title, fn))
        return fn
    return deco


def soft(app, ok: bool, msg: str) -> bool:
    return REPORT.check(ok, msg)


def anon_session(browser, with401hook: bool = False):
    """新建一个未登录上下文并返回 page（用于表单登录/匿名访问用例）。"""
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN")
    page = ctx.new_page()
    return ctx, page


def confirm_modal(page, timeout: int = 6000) -> bool:
    """确认自定义弹窗。

    项目全站用 ui.confirmModal（渲染到 #modal-root，确认键 [data-confirm]），
    源码中没有任何原生 confirm()/alert()。因此测试里挂 page.on("dialog") 是无效的：
    弹窗不会被确认，会一直遮住页面，导致后续点击全部超时。
    """
    try:
        page.wait_for_selector("#modal-root [data-confirm]", timeout=timeout)
        page.click("#modal-root [data-confirm]")
        return True
    except Exception:  # noqa: BLE001
        return False


def toast_text(page, timeout: int = 6000) -> str:
    import errno  # noqa: F401
    try:
        page.wait_for_selector("#toast-container .toast", timeout=timeout)
    except Exception:  # noqa: BLE001
        return ""
    time.sleep(0.15)
    els = page.query_selector_all("#toast-container .toast")
    return " / ".join(e.inner_text().strip() for e in els) if els else ""


# --------------------------------------------------------------------------
# HTTP 辅助（少量无需走 UI 的前置/校验，如创建测试数据）
# --------------------------------------------------------------------------
def http(method: str, path: str, token: str = "", payload=None, raw: bool = False):
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request

    # 2026-11-04：path 里可能带中文查询参数（keyword=研发规范）。
    # urllib 只接受 ASCII URL，不编码会抛 'ascii' codec can't encode，
    # 被下方 except 吞成 status=0 —— 表现为「明明有数据却查不到」。
    # 只对非 ASCII 部分转义，已编码的 %XX 保持不变。
    path = urllib.parse.quote(path, safe="/?&=%:+#")

    data = None
    headers = {}
    if payload is not None:
        data = _json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE_URL + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if raw:
                # 2026-11-04：raw 用于预览/下载这类**二进制**响应
                # （图片/PDF）。此前一律 .decode()，遇到非 UTF-8 字节就抛
                # UnicodeDecodeError，被 except 吞成 status=0。
                blob = resp.read()
                try:
                    text = blob.decode()
                except UnicodeDecodeError:
                    text = blob.decode("latin-1")  # 保留字节，仅便于断言长度/首字节
                return {"status": resp.status, "body": text,
                        "bytes": blob,
                        "content_type": resp.headers.get("Content-Type", "")}
            body = resp.read().decode()
            return {"status": resp.status, "body": _json.loads(body) if body else None}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            parsed = _json.loads(body) if body else None
        except Exception:  # noqa: BLE001
            parsed = body
        return {"status": e.code, "body": parsed}
    except Exception as e:  # noqa: BLE001
        return {"status": 0, "body": str(e)}


def api_login(creds: dict) -> dict:
    r = http("POST", "/api/v1/auth/login", "", creds)
    if r["status"] != 200:
        raise RuntimeError(f"登录失败 {creds['username']}: {r['status']} {r['body']}")
    return r["body"]


# --------------------------------------------------------------------------
# 浏览器会话封装
# --------------------------------------------------------------------------
class Session:
    """一个角色的一套浏览器上下文。

    注意（2026-09-14 实测）：系统实现了**单点登录**（每次登录 bump users.token_version），
    因此在任何其它 context 里重复登录同一账号，都会让本 context 的旧令牌立刻失效
    （app/core/deps.py:112）。本类通过 ensure() 自动探测并续签，避免用例互相踩踏。
    """

    def __init__(self, browser, creds: dict, name: str, console_bucket: list[str]):
        self.browser = browser
        self.ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        self.page = self.ctx.new_page()
        self.name = name
        self.creds = creds
        self.bucket = console_bucket
        self._token = ""
        self._ver_hint = None
        info = api_login(creds)
        self.token = info["access_token"]
        self.user = info.get("user", {})
        self.page.on("console", lambda m: console_bucket.append(f"[{name}][console.{m.type}] {m.text}")
                     if m.type == "error" else None)
        self.page.on("pageerror", lambda e: console_bucket.append(f"[{name}][pageerror] {e}"))

    # ---- 令牌（属性化，外部读取即是最新的）----
    @property
    def token(self) -> str:
        return self._token

    @token.setter
    def token(self, value: str) -> None:
        self._token = value
        try:
            self.ctx.add_cookies([{"name": "access_token", "value": value, "url": BASE_URL}])
        except Exception:  # noqa: BLE001
            pass

    def ensure(self) -> bool:
        """确认当前浏览器会话仍然有效；失效即刻重新登录。（单点登录设计所需）"""
        try:
            r = self.ctx.request.get(BASE_URL + "/api/v1/auth/me")
            if r.status == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            self.token = api_login(self.creds)["access_token"]
            return True
        except Exception:  # noqa: BLE001
            return False

    def resync(self) -> None:
        """强制重新签发令牌并写回 Cookie（跨 context 登录过同一账号后调用）。"""
        self.token = api_login(self.creds)["access_token"]

    # ---- 导航 ----
    def goto(self, url: str, wait_networkidle: bool = True, ensure_session: bool = True) -> None:
        if ensure_session:
            self.ensure()
        self.page.goto(BASE_URL + url if url.startswith("/") else url)
        if wait_networkidle:
            self.page.wait_for_load_state("networkidle")

    def ui_login(self) -> None:
        """走真实登录表单（而非注入 Cookie）。"""
        self.goto("/login")
        self.page.fill("#username", self.creds["username"])
        self.page.fill("#password", self.creds["password"])
        self.page.click("#login-submit")
        self.page.wait_for_load_state("networkidle")

    # ---- 常用原语 ----
    def rows(self, tbody: str, timeout: int = 10000) -> list:
        self.page.wait_for_selector(f"{tbody} tr", timeout=timeout)
        return self.page.query_selector_all(f"{tbody} tr")

    def row_count(self, tbody: str, timeout: int = 10000) -> int:
        try:
            return len(self.rows(tbody, timeout))
        except Exception:  # noqa: BLE001
            return 0

    def row_texts(self, tbody: str) -> list[str]:
        return [r.inner_text().replace("\n", " | ") for r in self.rows(tbody)]

    def toast_text(self, timeout: int = 6000) -> str:
        try:
            self.page.wait_for_selector("#toast-container .toast", timeout=timeout)
        except Exception:  # noqa: BLE001
            return ""
        time.sleep(0.15)
        els = self.page.query_selector_all("#toast-container .toast")
        return " / ".join(e.inner_text().strip() for e in els) if els else ""

    def modal(self, timeout: int = 5000):
        self.page.wait_for_selector("#modal-root .modal-overlay", timeout=timeout)
        return self.page.query_selector("#modal-root .modal-overlay")

    def shot(self, name: str) -> str:
        return REPORT.shot(self.page, name)

    def reload(self) -> None:
        self.page.reload()
        self.page.wait_for_load_state("networkidle")

    def fetch(self, method: str, path: str, payload=None):
        """在页面上下文里发请求（用于验证「前端授权之外还有后端拦截」）。"""
        return self.page.evaluate(
            "async ([m, p, body]) => {"
            "  const opt = {method: m, credentials: 'same-origin',"
            "               headers: {'Content-Type': 'application/json'}};"
            "  if (body) opt.body = JSON.stringify(body);"
            "  const r = await fetch(p, opt);"
            "  let j = null; try { j = await r.json(); } catch (e) {}"
            "  return {status: r.status, body: j};"
            "}",
            [method, path, payload],
        )


class App:
    """全局夹具容器。"""

    def __init__(self, browser, buckets: dict):
        self.browser = browser
        self.admin = Session(browser, ADMIN, "admin", buckets["admin"])
        self.user = Session(browser, NORMAL, "user", buckets["user"])
        self.state: dict = {}
        self.buckets = buckets
        self.noise_ok = False

    def allow_noise(self) -> None:
        """声明本用例会主动触发 4xx 资源错误（越权探测），不把其计为缺陷。"""
        self.noise_ok = True

    def resync_all(self) -> None:
        """跨 context 登录过同一账号后，刷新全部会话令牌。"""
        self.admin.resync()
        self.user.resync()


def launch(pw):
    # 默认使用系统 Chrome（公司部署环境已装，无需下载浏览器）。
    # 显式设置 E2E_BROWSER_CHANNEL="" 时改用 Playwright bundled Chromium，
    # 让 requirements-e2e.txt 的文档说明在本地无 Chrome 的机器上仍然成立。
    channel = os.environ.get("E2E_BROWSER_CHANNEL", "chrome") or None
    return pw.chromium.launch(
        channel=channel, headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox"],
    )


def run_selected(selected: set[str] | None = None, stop_on_error: bool = False) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = launch(pw)
        buckets = {"admin": [], "user": [], "misc": []}
        app = App(browser, buckets)
        for code, title, fn in CASES:
            if selected and not any(code.startswith(s) for s in selected):
                continue
            REPORT.start(code, title)
            app.noise_ok = False
            # 逐桶记录长度：不能用「拼接后切片」——那样新增 N 条时会从 user 桶尾部
            # 取 N 条，把上一用例的历史噪声（如越权 403）反复算到后续用例头上。
            snapshot = {k: len(v) for k, v in buckets.items()}
            try:
                fn(app)
            except Exception as exc:  # noqa: BLE001
                REPORT.error(exc)
                traceback.print_exc()
                REPORT.shot(app.admin.page, f"ERR_{code}.png")
            finally:
                new = []
                for _k, _v in buckets.items():
                    new.extend(_v[snapshot[_k]:])
                if new:
                    real = [m for m in new if app.noise_ok and "Failed to load resource" in m] and [] or new
                    if app.noise_ok:
                        real = [m for m in new if "Failed to load resource" not in m]
                    if real:
                        REPORT.fail("浏览器控制台出现错误：" + " ; ".join(real[:3]))
                REPORT.end()
                if stop_on_error and REPORT.rows[-1]["status"] != "PASS":
                    break
        browser.close()

    ok_n, fail_n, err_n = REPORT.summary()
    print(f"目标服务：{BASE_URL}")
    print(f"失败截图：{SHOT_DIR}")
    return 0 if (fail_n + err_n) == 0 else 1


if __name__ == "__main__":
    sys.exit(run_selected())
