"""全量矩阵安全回归：Host/Origin、下载缓存、回跳与锁定文案。

这些用例验证浏览器/HTTP 可观察行为，而不是只检查实现细节：
- CSRF 不得把请求自带的 Host 直接当成可信来源；
- 敏感资料文件响应不得进入共享缓存；
- 登录 next 与列表 return_to 只能回到本系统资料/通知列表；
- 已下线的通知确认功能不得在页面源码中留下活动文案。
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

from app.core.config import get_settings
from files.generators import DOCX_FILENAME, PDF_FILENAME, make_docx, make_pdf
from helpers import create_document, upload_document_file


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _deterministic_csrf_settings(monkeypatch):
    """固定安全配置，避免开发机 .env 改变测试语义。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "TRUST_REVERSE_PROXY_HEADERS", False)
    monkeypatch.setattr(
        settings,
        "TRUSTED_HOSTS",
        "localhost,*.localhost,127.0.0.1,::1,testserver",
    )


def _create_doc(client, headers, title: str = "安全矩阵资料") -> int:
    return create_document(
        client,
        headers,
        title=title,
        effective_from="2026-01-01",
    )["id"]


def _post_document(client, headers, title: str):
    return client.post(
        "/api/v1/documents",
        json={"title": title, "effective_from": "2026-01-01"},
        headers=headers,
    )


def _return_href(page: str) -> str:
    match = re.search(
        r'id="btn-return-list"[^>]*\bhref="([^"]+)"',
        page,
        flags=re.DOTALL,
    )
    assert match, "详情页必须渲染返回列表链接"
    return html.unescape(match.group(1))


class TestCsrfTrustBoundary:
    def test_untrusted_host_and_forwarded_host_do_not_bypass_csrf(
        self, client, admin_headers
    ):
        resp = _post_document(
            client,
            {
                **admin_headers,
                "Host": "evil.example",
                "X-Forwarded-Host": "evil.example",
                "Origin": "http://evil.example",
            },
            "伪造 Host 不得保存",
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "FORBIDDEN"

    def test_forwarded_host_is_ignored_when_proxy_trust_is_disabled(
        self, client, admin_headers
    ):
        assert get_settings().TRUST_REVERSE_PROXY_HEADERS is False
        resp = _post_document(
            client,
            {
                **admin_headers,
                "Host": "testserver",
                "X-Forwarded-Host": "evil.example",
                "Origin": "http://evil.example",
            },
            "转发头不得绕过 CSRF",
        )
        assert resp.status_code == 403, resp.text

    def test_forwarded_host_is_used_only_after_explicit_proxy_trust(
        self, client, admin_headers, monkeypatch
    ):
        settings = get_settings()
        monkeypatch.setattr(settings, "TRUST_REVERSE_PROXY_HEADERS", True)
        monkeypatch.setattr(
            settings,
            "TRUSTED_HOSTS",
            "testserver,proxy.example",
        )
        resp = _post_document(
            client,
            {
                **admin_headers,
                "Host": "testserver",
                "X-Forwarded-Host": "proxy.example",
                "X-Forwarded-Proto": "http",
                "Origin": "http://proxy.example",
            },
            "显式信任代理后允许同源",
        )
        assert resp.status_code == 201, resp.text

    @pytest.mark.parametrize(
        ("host", "header_name", "header_value"),
        [
            ("testserver", "Origin", "null"),
            ("testserver", "Origin", "https://evil.example"),
            ("testserver", "Origin", "http://testserver, http://evil.example"),
            ("testserver", "Origin", "http://testserver/path"),
            ("localhost:8000", "Origin", "http://localhost:9999"),
            ("testserver", "Referer", "https://evil.example/path"),
        ],
    )
    def test_cross_origin_null_origin_and_cross_port_are_rejected(
        self, client, admin_headers, host, header_name, header_value
    ):
        resp = _post_document(
            client,
            {
                **admin_headers,
                "Host": host,
                header_name: header_value,
            },
            "跨源请求不得保存",
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "FORBIDDEN"

    def test_same_origin_still_passes_csrf(self, client, admin_headers):
        resp = _post_document(
            client,
            {
                **admin_headers,
                "Host": "testserver",
                "Origin": "http://testserver",
            },
            "同源请求应通过",
        )
        assert resp.status_code == 201, resp.text


class TestSensitiveFileCaching:
    def test_attachment_file_and_preview_are_private_no_store(
        self, client, admin_headers, tmp_path
    ):
        doc_id = _create_doc(client, admin_headers, title="下载缓存边界")

        pdf = make_pdf(tmp_path / PDF_FILENAME).read_bytes()
        main = upload_document_file(
            client,
            admin_headers,
            doc_id,
            filename=PDF_FILENAME,
            content=pdf,
            mime="application/pdf",
        )
        assert main.status_code == 200, main.text

        docx = make_docx(tmp_path / DOCX_FILENAME).read_bytes()
        attachment = client.post(
            f"/api/v1/documents/{doc_id}/attachment",
            files={
                "file": (
                    DOCX_FILENAME,
                    docx,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers=admin_headers,
        )
        assert attachment.status_code == 200, attachment.text

        for path in (
            f"/api/v1/documents/{doc_id}/file",
            f"/api/v1/documents/{doc_id}/attachment",
            f"/api/v1/documents/{doc_id}/preview/pdf",
        ):
            resp = client.get(path, headers=admin_headers)
            assert resp.status_code == 200, f"{path}: {resp.status_code} {resp.text}"
            assert resp.headers.get("cache-control") == "private, no-store", path

        preview = client.get(
            f"/api/v1/documents/{doc_id}/preview/pdf",
            headers=admin_headers,
        )
        assert preview.headers["content-disposition"].startswith("inline;"), (
            "PDF 预览仍必须在浏览器内联展示"
        )


class TestReturnNavigationAllowlist:
    @pytest.mark.parametrize(
        "bad_next",
        [
            "https://evil.example/",
            "//evil.example/",
            "/\\evil.example",
            "/%5Cevil.example",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "/documents?x=1\r\nLocation: https://evil.example",
            "/documents?x=" + ("a" * 2049),
        ],
    )
    def test_login_rejects_unsafe_next(self, client, bad_next):
        resp = client.get(
            "/login",
            params={"next": bad_next},
            follow_redirects=False,
        )
        assert resp.status_code == 302, resp.text
        assert resp.headers["location"] == "/login"

    @pytest.mark.parametrize(
        "safe_next",
        [
            "/documents",
            "/documents?status=active&page=2",
            "/notices",
            "/notices?tab=unread&page=2",
        ],
    )
    def test_login_keeps_safe_list_next(self, client, safe_next):
        resp = client.get("/login", params={"next": safe_next})
        assert resp.status_code == 200

    @pytest.mark.parametrize(
        "bad_return_to",
        [
            "https://evil.example/",
            "//evil.example/",
            "/\\evil.example",
            "/%5Cevil.example",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "/documents?x=1\r\nLocation: https://evil.example",
            "/documents?x=" + ("a" * 2049),
            "/documents/1",
            "/admin/users",
        ],
    )
    def test_document_return_to_falls_back_to_safe_list(
        self, client, admin_headers, bad_return_to
    ):
        resp = client.get(
            "/documents/1",
            params={"return_to": bad_return_to},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert _return_href(resp.text) == "/documents"

    def test_return_to_preserves_only_allowlisted_path_and_query(
        self, client, admin_headers
    ):
        resp = client.get(
            "/notices/1",
            params={"return_to": "/notices?tab=unread&page=2"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        href = _return_href(resp.text)
        assert href == "/notices?tab=unread&page=2"
        assert not href.startswith(("http://", "https://", "//"))


def test_pages_and_templates_have_no_retired_confirmation_ui():
    roots = [
        PROJECT_ROOT / "app" / "routers" / "pages.py",
        PROJECT_ROOT / "app" / "web" / "templates",
    ]
    banned = (
        "确认按钮",
        "确认率",
        "待确认",
        "confirmed_at",
        "confirmed_rate",
        "requires_confirmation",
    )
    offenders: list[str] = []
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        else:
            files.extend(root.rglob("*.html"))
    for path in sorted(files):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            if token in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {token}")
    assert not offenders, "已下线的通知确认功能仍在页面源码中出现：" + ", ".join(offenders)