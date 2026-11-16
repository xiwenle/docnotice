"""M70 返回导航：安全 return_to、详情返回列表、编辑/统计语义与滚动恢复。"""
from __future__ import annotations

from urllib.parse import quote, urlsplit

from harness import REPORT, case


def _document_id(app) -> int | None:
    did = app.state.get("doc_pdf_id")
    if did:
        return did
    s = app.admin
    s.goto("/")
    body = s.fetch("GET", "/api/v1/documents?page_size=1").get("body") or {}
    items = body.get("items") or []
    return items[0]["id"] if items else None


def _notice_id(app) -> int | None:
    nid = app.state.get("notice_published_id")
    if nid:
        return nid
    s = app.admin
    s.goto("/")
    body = s.fetch("GET", "/api/v1/notices?page_size=1").get("body") or {}
    items = body.get("items") or []
    return items[0]["id"] if items else None


def _return_href(page) -> str:
    page.wait_for_selector("#btn-return-list", timeout=15000)
    button = page.locator("#btn-return-list")
    REPORT.check(button.is_visible(), "详情页返回列表按钮可见")
    REPORT.check("返回" in (button.inner_text() or ""), "详情页返回按钮文案明确")
    return button.get_attribute("href") or ""


def _assert_no_visible_breadcrumb(page, label: str) -> None:
    count = page.locator(".page-main .breadcrumb:visible").count()
    REPORT.check(count == 0, f"{label}不恢复重复页内面包屑（可见数量={count}）")


@case("RN01", "资料详情：安全 return_to 保留 query，非法值回落资料列表")
def rn01(app):
    did = _document_id(app)
    if not did:
        REPORT.fail("无可用资料")
        return
    s = app.admin
    safe = "/documents?status=active&keyword=%E5%91%98%E5%B7%A5"
    s.goto(f"/documents/{did}?return_to={quote(safe, safe='')}")
    href = _return_href(s.page)
    parsed = urlsplit(href)
    REPORT.check(parsed.path == "/documents", f"安全 return_to 返回列表：{parsed.path}")
    REPORT.check(
        parsed.query == "status=active&keyword=%E5%91%98%E5%B7%A5",
        f"保留原 query：{parsed.query}",
    )
    _assert_no_visible_breadcrumb(s.page, "资料详情")

    invalid = [
        "https://evil.example/documents",
        "//evil.example/documents",
        "/admin/users",
        "/documents/123",
        "javascript:alert(1)",
    ]
    for raw in invalid:
        s.goto(f"/documents/{did}?return_to={quote(raw, safe='')}")
        fallback = _return_href(s.page)
        parsed = urlsplit(fallback)
        REPORT.check(
            parsed.path == "/documents" and not parsed.query and not parsed.scheme and not parsed.netloc,
            f"非法 return_to 回落 /documents：{raw!r} → {fallback!r}",
        )


@case("RN02", "通知详情：安全 return_to 保留 query，非法值回落通知列表")
def rn02(app):
    nid = _notice_id(app)
    if not nid:
        REPORT.fail("无可用通知")
        return
    s = app.admin
    safe = "/notices?tab=unread&page=2"
    s.goto(f"/notices/{nid}?return_to={quote(safe, safe='')}")
    href = _return_href(s.page)
    parsed = urlsplit(href)
    REPORT.check(parsed.path == "/notices", f"安全 return_to 返回列表：{parsed.path}")
    REPORT.check(parsed.query == "tab=unread&page=2", f"保留原 query：{parsed.query}")
    _assert_no_visible_breadcrumb(s.page, "通知详情")

    s.goto(f"/notices/{nid}?return_to={quote('/admin/audit-logs', safe='')}")
    parsed = urlsplit(_return_href(s.page))
    REPORT.check(
        parsed.path == "/notices" and not parsed.query,
        "非列表路径回落 /notices",
    )


@case("RN03", "列表详情链接自动携带当前列表 return_to（含 query）")
def rn03(app):
    s = app.admin
    s.goto("/documents?status=active")
    try:
        s.page.wait_for_selector(".doc-title-link", timeout=15000)
        s.page.wait_for_function(
            "() => {const a=document.querySelector('.doc-title-link');"
            " return !!a && !!new URL(a.href).searchParams.get('return_to');}",
            timeout=7000,
        )
        got = s.page.eval_on_selector(
            ".doc-title-link",
            "a => new URL(a.href).searchParams.get('return_to')",
        )
        current = s.page.evaluate("location.pathname + location.search")
        REPORT.check(got == current, f"资料链接 return_to 等于当前列表：{got!r}")
    except Exception:  # noqa: BLE001
        REPORT.fail("资料列表未渲染可校验的详情链接")

    s.goto("/notices")
    try:
        s.page.wait_for_selector(".notice-title a", timeout=15000)
        s.page.wait_for_function(
            "() => {const a=document.querySelector('.notice-title a');"
            " return !!a && !!new URL(a.href).searchParams.get('return_to');}",
            timeout=7000,
        )
        got = s.page.eval_on_selector(
            ".notice-title a",
            "a => new URL(a.href).searchParams.get('return_to')",
        )
        current = s.page.evaluate("location.pathname + location.search")
        REPORT.check(got == current, f"通知链接 return_to 等于当前列表：{got!r}")
    except Exception:  # noqa: BLE001
        REPORT.fail("通知列表未渲染可校验的详情链接")


@case("RN04", "编辑/统计页：返回详情与返回列表语义明确")
def rn04(app):
    did = _document_id(app)
    nid = _notice_id(app)
    if not did or not nid:
        REPORT.fail("缺少资料或通知样本")
        return
    s = app.admin

    s.goto(f"/admin/documents/{did}/edit")
    REPORT.check(
        s.page.get_attribute('.page-header-actions a[href="/documents/%s"]' % did, "href") == f"/documents/{did}",
        "资料编辑页提供「返回资料详情」",
    )
    REPORT.check(
        s.page.get_attribute('.page-header-actions a[href="/documents"]', "href") == "/documents",
        "资料编辑页提供「返回资料列表」",
    )
    REPORT.check(
        s.page.get_attribute(".form-actions-sticky a.btn-secondary", "href") == f"/documents/{did}",
        "资料编辑页「取消」返回详情",
    )
    _assert_no_visible_breadcrumb(s.page, "资料编辑页")

    s.goto(f"/admin/notices/{nid}/edit")
    REPORT.check(
        s.page.get_attribute('.page-header-actions a[href="/notices/%s"]' % nid, "href") == f"/notices/{nid}",
        "通知编辑页提供「返回通知详情」",
    )
    REPORT.check(
        s.page.get_attribute('.page-header-actions a[href="/notices"]', "href") == "/notices",
        "通知编辑页提供「返回通知列表」",
    )
    REPORT.check(
        s.page.get_attribute(".form-actions-sticky a.btn-ghost", "href") == f"/notices/{nid}",
        "通知编辑页「取消」返回详情",
    )
    _assert_no_visible_breadcrumb(s.page, "通知编辑页")

    s.goto(f"/admin/notices/{nid}/stats")
    REPORT.check(
        s.page.get_attribute('.page-header-actions a[href="/notices/%s"]' % nid, "href") == f"/notices/{nid}",
        "统计页提供「返回通知详情」",
    )
    REPORT.check(
        s.page.get_attribute('.page-header-actions a[href="/notices"]', "href") == "/notices",
        "统计页提供「返回通知列表」",
    )
    _assert_no_visible_breadcrumb(s.page, "阅读统计页")


@case("RN05", "从资料列表进入详情后返回，尽量恢复原滚动位置")
def rn05(app):
    s = app.admin
    s.goto("/documents")
    try:
        s.page.wait_for_selector(".doc-title-link", timeout=15000)
        links = s.page.locator(".doc-title-link")
        index = min(8, links.count() - 1)
        link = links.nth(max(0, index))
        link.scroll_into_view_if_needed()
        s.page.wait_for_timeout(300)
        before = s.page.evaluate("window.scrollY")
        if before < 80:
            REPORT.note(f"页面可滚动距离不足，跳过严格位置比较（scrollY={before}）")
            return
        link.click()
        s.page.wait_for_url("**/documents/*", timeout=15000)
        _return_href(s.page)
        s.page.click("#btn-return-list")
        s.page.wait_for_url("**/documents", timeout=15000)
        s.page.wait_for_selector(".doc-title-link", timeout=15000)
        s.page.wait_for_timeout(2200)
        after = s.page.evaluate("window.scrollY")
        REPORT.check(
            abs(after - before) <= 12,
            f"返回后恢复滚动位置：{before:.0f} → {after:.0f}",
        )
    except Exception as exc:  # noqa: BLE001
        REPORT.fail(f"滚动恢复流程失败：{exc}")
