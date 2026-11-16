"""M12 资料详情：渲染完整性、PDF/DOCX/XLSX/XLS 在线预览、下载权限、按日期查状态（§12/§15）。"""
from __future__ import annotations

from harness import REPORT, SHOT_DIR, case


def _detail(app, did, role="admin"):
    s = app.admin if role == "admin" else app.user
    s.goto(f"/documents/{did}")
    s.page.wait_for_selector("#detail-root", timeout=15000)
    s.page.wait_for_timeout(1800)
    return s


@case("G01", "资料详情：元信息齐全（标题/状态/分类/标签/版本/文件）")
def g01(app):
    did = app.state.get("doc_pdf_id")
    if not did:
        REPORT.fail("无可用 PDF 资料（依赖 F03）")
        return
    s = _detail(app, did)
    txt = s.page.inner_text("#detail-root")
    for kw in ("生效区间", "分类：", "标签：", "版本："):
        REPORT.check(kw in txt, f"详情页包含「{kw}」")
    REPORT.check("编号" not in txt, "资料编号已移除，详情页不应出现「编号」")
    REPORT.check("员工手册2026.pdf" in txt, "显示原始文件名")
    REPORT.check(s.page.query_selector(".doc-header .doc-summary") is None,
                 "资料说明不再挂在标题下方")
    REPORT.check(app.state.get("doc_pdf_id") in s.page.url, f"URL 指向正确资料：{s.page.url}")
    head = s.page.inner_text("#bc-title").strip()
    REPORT.check(len(head) > 0, f"面包屑标题已填充：{head[:30]}")
    s.page.screenshot(path=str(SHOT_DIR / "G01_detail_pdf.png"))


@case("G02", "PDF 在线预览：iframe + blob 数据源加载成功")
def g02(app):
    did = app.state.get("doc_pdf_id")
    if not did:
        REPORT.fail("无可用 PDF 资料（依赖 F03）")
        return
    s = _detail(app, did)
    frame = s.page.query_selector("#preview-body iframe.pdf-frame")
    REPORT.check(frame is not None, "渲染出 PDF iframe")
    if frame:
        src = frame.get_attribute("src") or ""
        REPORT.check(src.startswith("blob:"), f"数据源为 blob（避免 iframe 二次请求 401）：{src[:24]}")
    err = s.page.evaluate(
        "() => {const b = document.getElementById('preview-body');"
        " return b ? b.innerText.includes('无法在线预览') : true;}")
    REPORT.check(not err, "未出现「无法在线预览」兜底态")
    REPORT.check(s.page.query_selector("#pdf-open-tab") is not None, "提供「全屏查看」按钮")


@case("G03", "DOCX 预览：结构化渲染且无 HTML 注入风险")
def g03(app):
    did = app.state.get("doc_docx_id")
    if not did:
        REPORT.fail("无可用 DOCX 资料（依赖 F04）")
        return
    s = _detail(app, did)
    body = s.page.query_selector("#preview-body .docx-preview")
    REPORT.check(body is not None, "渲染出 DOCX 结构化预览容器")
    text = body.inner_text() if body else ""
    REPORT.check(len(text.strip()) > 0, f"预览有内容（{len(text)} 字符）")
    headings = s.page.eval_on_selector_all("#preview-body .docx-preview h1, #preview-body .docx-preview h2", "els => els.length")
    REPORT.check(headings >= 1, f"标题层级被保留（{headings} 个标题）")
    paragraphs = s.page.eval_on_selector_all("#preview-body .docx-preview p", "els => els.length")
    REPORT.check(paragraphs >= 1, f"段落结构被保留（{paragraphs} 个段落）")
    unsafe = s.page.evaluate(
        "() => {const b = document.querySelector('#preview-body .docx-preview');"
        " return b ? !!b.querySelector('script, iframe, object, embed, img, svg, [onload], [onerror]') : true;}")
    REPORT.check(not unsafe, "预览节点中无可执行/外部资源标签")
    s.page.screenshot(path=str(SHOT_DIR / "G03_detail_docx.png"))


@case("G04", "XLSX / XLS 预览：表格结构渲染")
def g04(app):
    for label, key in (("XLSX", "doc_xlsx_id"), ("XLS", "doc_xls_id")):
        did = app.state.get(key)
        if not did:
            REPORT.fail(f"无可用 {label} 资料（依赖 F04）")
            continue
        s = _detail(app, did)
        body_html = s.page.inner_html("#preview-body")
        REPORT.check("table" in body_html.lower(), f"{label} 预览渲染出表格结构")
        txt = s.page.inner_text("#preview-body")
        REPORT.check(not txt.strip().startswith("正在加载"), f"{label} 预览加载完成")
        err = "无法在线预览" in txt or "不支持" in txt
        REPORT.check(not err, f"{label} 无预览失败兜底文案")


@case("G05", "下载权限：可见即可下载（allow_download 开关已移除）")
def g05(app):
    """2026-11-04 产品决策：下载权限 = 查看权限。

    原 G05/G06 分别验证 allow_download=false/true 的差异，该开关已整体移除
    （服务端不再读写、表单不再渲染复选框），两条合并为「可见即可下载」：
    普通用户既能看到下载入口，也能真的取到文件流。
    """
    did = app.state.get("doc_xlsx_id")
    if not did:
        REPORT.fail("无可用资料（依赖 F04）")
        return
    s = _detail(app, did, role="user")
    html = s.page.inner_html("#detail-root")
    REPORT.check(f'/api/v1/documents/{did}/file' in html,
                 "普通用户渲染出下载入口（可见即可下载）")
    r = app.user.fetch("GET", f"/api/v1/documents/{did}/file")
    REPORT.check(r["status"] == 200, f"普通用户可取文件流：{r['status']}")

    # 表单里不应再有下载权限开关（防止功能被悄悄加回来）
    from harness import BASE_URL

    s.page.goto(f"{BASE_URL}/admin/documents/{did}/edit")
    s.page.wait_for_timeout(1200)
    REPORT.check(s.page.query_selector("#f-allow-download") is None,
                 "编辑页不再有「下载权限」开关")
    app.state["no_download_doc"] = did
    s.page.screenshot(path=str(SHOT_DIR / "G05_download_allowed.png"))


@case("G07", "详情页按日期查询历史状态（项目规范 §15 闭区间规则）")
def g07(app):
    did = app.state.get("doc_pdf_id")
    if not did:
        REPORT.fail("无可用 PDF 资料（依赖 F03）")
        return
    s = _detail(app, did)

    def query(d: str) -> str:
        s.page.fill("#status-date", d)
        s.page.click("#btn-status-query")
        s.page.wait_for_timeout(900)
        return s.page.inner_text("#status-result").strip()

    # 该资料 effective_from=2026-01-01，effective_to 为空
    REPORT.check("未生效" in query("2025-12-31"), f"生效前一天(2025-12-31) → 未生效：{query('2025-12-31')[:24]}")
    REPORT.check("生效中" in query("2026-01-01"), f"生效当天(2026-01-01) → 生效中：{query('2026-01-01')[:24]}")
    REPORT.check("生效中" in query("2026-01-02"), f"生效后一天(2026-01-02) → 生效中：{query('2026-01-02')[:24]}")
    long_day = query("2099-01-01")
    REPORT.check("生效中" in long_day or "已过期" in long_day,
                 f"无失效日期的远期查询：{long_day[:30]}")
    s.page.screenshot(path=str(SHOT_DIR / "G07_status_query.png"))


@case("G08", "详情页：管理员可见编辑/替换/删除，普通用户不可见")
def g08(app):
    did = app.state.get("doc_pdf_id")
    if not did:
        REPORT.fail("无可用 PDF 资料（依赖 F03）")
        return
    s = _detail(app, did)
    for bid, label in (("btn-upload-file", "上传/替换文件"),
                       ("btn-archive", "删除资料")):
        REPORT.check(s.page.query_selector(f"#{bid}") is not None, f"管理员可见「{label}」")
    REPORT.check(s.page.query_selector("a[href*='/admin/documents/']") is not None, "管理员可见编辑入口")
    u = _detail(app, did, role="user")
    for bid in ("btn-upload-file", "btn-archive", "btn-restore"):
        REPORT.check(u.page.query_selector(f"#{bid}") is None, f"普通用户无 #{bid}")


@case("G09", "资料说明：普通用户可检索摘要、可查看低调折叠说明")
def g09(app):
    import time
    from harness import http

    keyword = f"摘要可检索标记{int(time.time())}"
    summary = "第一行：" + keyword + "\n第二行：普通用户也能展开查看"
    title = f"E2E普通用户说明{int(time.time())}"
    created = http("POST", "/api/v1/documents", app.admin.token, {
        "title": title,
        "summary": summary,
        "effective_from": "2026-01-01",
        "audience_scope": "all",
    })
    REPORT.check(created["status"] in (200, 201), f"创建带摘要资料：{created['status']}")
    did = (created.get("body") or {}).get("id")
    if not did:
        return

    me = http("GET", "/api/v1/auth/me", app.user.token)
    me_status = me.get("status")
    me_body = me.get("body") or {}
    REPORT.check(me_status == 200 and me_body.get("role") == "user",
                 f"普通用户浏览器上下文核验：{me_status} / {me_body.get('role')}")

    u = app.user
    u.goto("/documents")
    u.page.fill("#f-keyword", keyword)
    u.page.click("#btn-search")
    u.page.wait_for_function(
        "() => !document.querySelector('#doc-tbody .skeleton')",
        timeout=15000,
    )
    u.page.wait_for_timeout(500)
    rows = u.page.eval_on_selector_all(
        "#doc-tbody .doc-title-link",
        "els => els.map(e => e.textContent.trim())",
    )
    REPORT.check(title in rows, f"普通用户可用说明摘要关键词搜到资料：{rows[:2]}")

    u.goto(f"/documents/{did}")
    u.page.wait_for_selector("#detail-root .doc-summary-disclosure", timeout=15000)
    fold = u.page.query_selector("#detail-root .doc-summary-disclosure")
    REPORT.check(fold is not None, "普通用户可见资料说明折叠入口")
    REPORT.check(not fold.evaluate("e => e.open"), "普通用户看到的资料说明默认折叠")
    u.page.click("#detail-root .doc-summary-disclosure > summary")
    content = u.page.inner_text("#detail-root .doc-summary-content")
    REPORT.check(keyword in content and "第二行" in content,
                 "普通用户展开后可查看摘要且换行保留")
    REPORT.check(u.page.query_selector("#detail-root .doc-header .doc-summary") is None,
                 "普通用户详情页标题下面不再显示说明")
    overflow = u.page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    REPORT.check(overflow, "资料说明展开后页面无横向溢出")
