"""M50 安全专项：XSS、路径穿越、超大文件、损坏文件、越权、注入尝试。

对应 项目规范 §11（文件安全）、§33（权限必须后端验证）、§32（文件测试）。
全部用例产生预期 4xx 时自行豁免 console 噪声。
"""
from __future__ import annotations

import re
import time

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))  # _paths.py
from harness import REPORT, SHOT_DIR, case, http

from _paths import E2E_FILES_DIR as FILES  # noqa: E402
BIG = FILES / "超大文件.pdf"          # 超出 MAX_UPLOAD_SIZE_MB
BROKEN = FILES / "损坏的.pdf"          # PDF 魔数正确但内容残破
TRAVERSAL = FILES / "..%2f..%2f机密.pdf"
XSS_TITLE = "<img src=x onerror=alert('xss')>E2E注入"
XSS_SCRIPT = "<script>window.__pwned=1</script>"
XSS_SAFE = "E2EXSS安全"


def _ensure_special_files():
    FILES.mkdir(parents=True, exist_ok=True)
    # 超大 PDF（60MB）：魔数正确、体积超限
    if not BIG.exists():
        with open(BIG, "wb") as f:
            f.write(b"%PDF-1.4\n")
            f.write(b"%" + b"0" * (1024 * 1024 - 1))
            chunk = b"0" * (1024 * 1024)
            for _ in range(59):
                f.write(chunk)
            f.write(b"\n%%EOF\n")
    # 损坏 PDF：有魔数但无有效结构
    if not BROKEN.exists():
        BROKEN.write_bytes(b"%PDF-1.4\n\x00\x01\x02garbage-without-trailer")
    return BIG.stat().st_size


def _new_form(s):
    s.goto("/admin/documents/new")
    s.page.wait_for_selector("#f-title", timeout=12000)
    return s


@case("S01", "XSS：资料标题/说明注入脚本不得执行、仅按纯文本渲染")
def s01(app):
    s = _new_form(app.admin)
    payload = XSS_TITLE + str(int(time.time()))
    s.page.on("dialog", lambda d: (REPORT.fail("出现 alert 弹窗，XSS 可执行"), d.dismiss()))
    s.page.fill("#f-title", payload)
    s.page.fill("#f-effective-from", "2026-01-01")
    s.page.fill("#f-summary", XSS_SCRIPT)
    s.page.click("#btn-submit")
    s.page.wait_for_url(re.compile(r".*/documents/\d+"), timeout=25000)
    s.page.wait_for_load_state("networkidle")
    did = re.search(r"/documents/(\d+)", s.page.url).group(1)

    # 1) 说明低调折叠：默认收起，展开后仍按纯文本渲染，不得真正生成可执行节点
    fold = s.page.query_selector(".doc-summary-disclosure")
    REPORT.check(fold is not None, "资料说明折叠块存在且可发现")
    if fold:
        REPORT.check(not fold.evaluate("e => e.open"), "资料说明默认折叠")
        s.page.click(".doc-summary-disclosure > summary")
    injected = s.page.evaluate(
        "() => ({img: document.querySelectorAll('img[src=\"x\"]').length,"
        " script: document.querySelectorAll('#detail-root script').length,"
        " scriptsInSummary: document.querySelectorAll('#detail-root .doc-summary-content script').length,"
        " summaryText: (document.querySelector('#detail-root .doc-summary-content') || {}).textContent || '',"
        " pwned: window.__pwned === 1})")
    REPORT.check(injected["scriptsInSummary"] == 0, "资料说明折叠区内未生成 <script> 节点")
    REPORT.check("<script>window.__pwned=1</script>" in injected["summaryText"],
                 "说明中的脚本标签按纯文本显示，不解释为 HTML")
    REPORT.check(injected["img"] == 0, f"详情页未生成 <img src=x> 节点（{injected['img']} 个）")
    REPORT.check(injected["script"] == 0, f"详情页未注入 <script>（{injected['script']} 个）")
    REPORT.check(not injected["pwned"], "注入的脚本未执行（window.__pwned 未置位）")
    # 2) 标题原样显示为文本
    REPORT.check(payload in s.page.inner_text("#detail-root")
                 or payload in s.page.inner_text("body"),
                 "标题作为纯文本完整呈现（未被吞掉，也未被解析）")
    app.state["xss_doc_id"] = did

    # 列表页同样转义
    s.goto("/documents")
    s.page.wait_for_selector("#doc-tbody tr", timeout=15000)
    s.page.fill("#f-keyword", "E2E注入")
    s.page.click("#btn-search")
    s.page.wait_for_timeout(2000)
    lst = s.page.evaluate(
        "() => ({img: document.querySelectorAll('img[src=\"x\"]').length,"
        " rows: document.querySelectorAll('#doc-tbody tr').length})")
    REPORT.check(lst["img"] == 0, f"列表页未生成可执行节点（{lst['img']} 个）")
    REPORT.check(lst["rows"] > 0, f"列表页能检索到该资料（{lst['rows']} 行）")
    s.page.screenshot(path=str(SHOT_DIR / "S01_xss_escaped.png"))


@case("S02", "XSS：通知标题/正文注入同样被转义")
def s02(app):
    s = app.admin
    payload = "<svg onload=alert(1)>E2E通知注入" + str(int(time.time()))
    created = http("POST", "/api/v1/notices", app.admin.token, {
        "title": payload,
        "content": XSS_SCRIPT + "<b>加粗也应该被转义</b>",
        "notice_type": "发布",
        "target": {"scope": "all"},
    })
    REPORT.check(created["status"] in (200, 201), f"注入型通知已创建：{created['status']}")
    if created["status"] not in (200, 201):
        return
    nid = (created["body"] or {}).get("id")
    # 2026-11-04 起创建即发布，无需单独的 publish 调用。
    s.goto("/notices")
    s.page.wait_for_selector("body", timeout=10000)
    s.page.wait_for_timeout(1500)
    injected = s.page.evaluate(
        "() => ({svg: document.querySelectorAll('svg[onload]').length,"
        " script: document.querySelectorAll('script').length,"
        " pwned: window.__pwned === 1})")
    REPORT.check(injected["svg"] == 0, f"通知列表未生成 <svg onload>（{injected['svg']} 个）")
    REPORT.check(not injected["pwned"], "通知侧注入脚本未执行")
    s.goto(f"/notices/{nid}")
    s.page.wait_for_timeout(1500)
    d = s.page.evaluate("() => ({svg: document.querySelectorAll('svg[onload]').length,"
                        " b: document.querySelectorAll('#notice-detail-root b, b').length})")
    REPORT.check(d["svg"] == 0, f"通知详情页未生成可执行节点（{d['svg']} 个）")
    http("DELETE", f"/api/v1/notices/{nid}", app.admin.token)


@case("S03", "路径穿越：上传名含 ../（含 %2F 编码形态）被清洗，无法逃出业务目录")
def s03(app):
    app.allow_noise()
    base = (FILES / "员工手册2026.pdf").read_bytes()
    cases = [
        ("../../../../../../etc/passwd.pdf", "原始 ../ 形态"),
        ("..%2F..%2F..%2Fetc%2Fpasswd.pdf", "百分号编码形态"),
        ("..\\..\\..\\机密.pdf", "反斜杠形态"),
    ]
    for raw_name, desc in cases:
        s = _new_form(app.admin)
        s.page.fill("#f-title", f"E2E穿越-{desc}-{int(time.time())}")
        s.page.fill("#f-effective-from", "2026-01-01")
        s.page.set_input_files("#upload-input", {
            "name": raw_name, "mimeType": "application/pdf", "buffer": base,
        })
        s.page.click("#btn-submit")
        s.page.wait_for_url(re.compile(r".*/documents/\d+"), timeout=25000)
        s.page.wait_for_load_state("networkidle")
        did = re.search(r"/documents/(\d+)", s.page.url).group(1)
        info = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
        cur = info.get("current_file") or {}
        disp = str(cur.get("original_filename") or "")
        REPORT.check("../" not in disp and "..\\" not in disp and "%2f" not in disp.lower(),
                     f"{desc}：展示名已清洗 → {disp[:40]}")
        REPORT.check(disp.endswith(".pdf"), f"{desc}：扩展名保留 → {disp[:40]}")
        # 接口不得向前端泄漏存储路径。
        # 判据：值里出现路径分隔符（/ 或 \）或 URL 编码的 %2f/%5c 才算泄漏；
        # 仅含 ".." 的清洗后文件名（.._.._机密.pdf）没有分隔符，不构成穿越。
        # 只查「路径/文件名类」字段：mime_type=application/pdf 合法地含 "/"，不算泄漏
        PATHY = ("path", "filename", "name", "url", "dir", "location")

        def _leaks(k: str, v: str) -> bool:
            if not any(t in k.lower() for t in PATHY):
                return False
            if "\\" in v or "/" in v:
                return True
            low = v.lower()
            return "%2f" in low or "%5c" in low or low.startswith("file:")
        leaked = [f"{k}={v}" for k, v in cur.items()
                  if isinstance(v, str) and _leaks(k, v)]
        REPORT.check(not leaked, f"{desc}：接口未泄漏存储路径（可疑字段 {leaked[:1]}）")

    # 直接构造穿越 URL 取业务文件
    for path in (
        "/static/../data/database/app.db",
        "/data/database/app.db",
        "/api/v1/documents/1/file/../../../../etc/passwd",
    ):
        r = http("GET", path, app.admin.token, raw=True)
        REPORT.check(r["status"] in (401, 403, 404, 422, 307, 308),
                     f"穿越/直取路径被拒：{path} → {r['status']}")


@case("S04", "超大文件：超过 MAX_UPLOAD_SIZE_MB 被拒且不落半截文件")
def s04(app):
    app.allow_noise()
    size = _ensure_special_files()
    s = app.admin
    s.goto("/documents/1" if False else "/")
    limit = s.page.evaluate("() => 50")
    s.goto("/admin/documents/new")
    s.page.wait_for_selector("#f-title", timeout=12000)
    s.page.fill("#f-title", f"E2E超大文件{int(time.time())}")
    s.page.fill("#f-effective-from", "2026-01-01")
    s.page.set_input_files("#upload-input", str(BIG))
    s.page.wait_for_timeout(1000)
    s.page.click("#btn-submit")
    s.page.wait_for_timeout(15000)
    toast = s.toast_text(8000)
    url = s.page.url
    if re.search(r"/documents/\d+", url):
        did = re.search(r"/documents/(\d+)", url).group(1)
        info = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
        REPORT.check(not info.get("has_file"),
                     f"{size // 1024 // 1024}MB 超限文件未入库（has_file={info.get('has_file')}）")
        REPORT.check("上传失败" in s.page.inner_text("body")
                     or "暂未上传文件" in s.page.inner_text("body"),
                     "页面明示上传未成功")
    else:
        REPORT.check(bool(toast) or "/admin/documents/new" in url,
                     f"超限被拦在表单侧（{size // 1024 // 1024}MB > {limit}MB，提示：{toast[:40]}）")


@case("S05", "损坏文件：魔数通过但内容不可解析时降级展示，不修改原始文件")
def s05(app):
    app.allow_noise()  # 服务端 415 属预期：结构损坏应在上传阶段拒绝
    s = app.admin
    s.goto("/admin/documents/new")
    s.page.wait_for_selector("#f-title", timeout=12000)
    title = f"E2E损坏文件{int(time.time())}"
    s.page.fill("#f-title", title)
    s.page.fill("#f-effective-from", "2026-01-01")
    s.page.set_input_files("#upload-input", str(BROKEN))
    s.page.click("#btn-submit")
    s.page.wait_for_url(re.compile(r".*/documents/\d+"), timeout=25000)
    s.page.wait_for_load_state("networkidle")
    did = re.search(r"/documents/(\d+)", s.page.url).group(1)
    info = http("GET", f"/api/v1/documents/{did}", app.admin.token)["body"] or {}
    if info.get("has_file"):
        # 原始字节必须原封不动
        raw = http("GET", f"/api/v1/documents/{did}/file", app.admin.token, raw=True)
        REPORT.check(raw["status"] == 200 and str(raw["body"]).startswith("%PDF"),
                     f"损坏文件仍可原样下载且未被改写：{raw['status']}")
        s.goto(f"/documents/{did}")
        s.page.wait_for_timeout(2500)
        body = s.page.inner_text("body")
        REPORT.check("预览" in body or "下载" in body, "详情页提供降级出口（预览/下载）")
        REPORT.check("无法" in body or "暂不支持" in body or "失败" in body
                     or "预览" in body, "损坏内容未导致页面白屏")
    else:
        REPORT.note("损坏文件在魔数/结构校验阶段即被拒，无需降级展示")


@case("S06", "越权：页内 fetch（绕过前端隐藏）仍被后端拦截")
def s06(app):
    app.allow_noise()
    s = app.user
    s.goto("/documents")
    s.page.wait_for_load_state("networkidle")
    probes = [
        ("POST", "/api/v1/documents", {"title": "越权新建", "effective_from": "2026-01-01"}),
        ("POST", "/api/v1/notices", {"title": "越权通知", "content": "x", "target": {"scope": "all"}}),
        ("GET", "/api/v1/users", None),
        ("GET", "/api/v1/audit-logs", None),
        ("GET", "/api/v1/recycle-bin", None),
        ("POST", "/api/v1/backups", {"type": "database"}),
        ("POST", "/api/v1/categories", {"name": "越权分类"}),
        ("POST", "/api/v1/tags", {"name": "越权标签"}),
        ("POST", "/api/v1/departments", {"name": "越权部门"}),
        ("POST", "/api/v1/recycle-bin/purge-all", {}),
    ]
    bad = []
    for method, path, payload in probes:
        r = s.fetch(method, path, payload)
        if r.get("status") not in (401, 403):
            bad.append(f"{method} {path} → {r.get('status')}")
    REPORT.check(not bad, "全部管理员接口对普通用户返回 401/403" + (f"；例外：{bad}" if bad else ""))


@case("S07", "注入尝试：搜索/筛选参数注入 SQL 片段不得报错或绕过")
def s07(app):
    from urllib.parse import urlencode

    s = app.admin
    for payload in ("' OR 1=1 --", "1' UNION SELECT * FROM users --", "%'--",
                    "'; DROP TABLE documents; --"):
        qs = urlencode({"keyword": payload, "page": 1, "page_size": 5})
        r = http("GET", f"/api/v1/documents?{qs}", app.admin.token)
        REPORT.check(r["status"] in (200, 201, 400, 422),
                     f"keyword={payload[:16]!r} → {r['status']}（未 500）")
        if r["status"] == 200:
            got = (r["body"] or {}).get("total")
            REPORT.check(isinstance(got, int) and got >= 0,
                         f"keyword={payload[:16]!r} 正常返回 total={got}")
    s.goto("/documents")
    s.page.wait_for_selector("#doc-tbody tr", timeout=15000)
    s.page.fill("#f-keyword", "' OR 1=1 --")
    s.page.click("#btn-search")
    s.page.wait_for_timeout(2000)
    body = s.page.inner_text("body")
    REPORT.check("Traceback" not in body and "Internal Server Error" not in body,
                 "页面未泄漏异常堆栈")
    s.page.click("#btn-reset")
    s.page.wait_for_timeout(1000)


@case("S08", "静态资源：仅 /static 暴露，程序代码与数据目录不可直取")
def s08(app):
    app.allow_noise()
    for path, ok_expect in (
        ("/static/js/ui.js", True),
        ("/static/css/style.css", True),
        ("/app/main.py", False),
        ("/app/core/config.py", False),
        ("/data/database/app.db", False),
        ("/requirements.txt", False),
        ("/.env", False),
        ("/.env.example", False),
    ):
        # raw=True：这些响应不是 JSON，不能用默认的 json 解析
        r = http("GET", path, app.admin.token, raw=True)
        REPORT.check((r["status"] == 200) == ok_expect,
                     f"{path} → {r['status']}（期望{'可访问' if ok_expect else '不可访问'}）")
