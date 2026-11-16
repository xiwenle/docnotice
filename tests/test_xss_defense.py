"""XSS 防御测试：确保注入到表单中的恶意脚本被正确转义。

验证后端 API 返回的 JSON 中 XSS payload 不会被前端不安全地渲染。
"""
from __future__ import annotations


import pytest

from tests.files.generators import make_pdf


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# XSS Payload 集合
XSS_PAYLOADS = [
    '<script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    '<svg onload=alert(1)>',
    '"><script>alert(document.cookie)</script>',
    "javascript:alert(1)",
    '<iframe src="javascript:alert(1)">',
    '<body onload=alert(1)>',
    '{{constructor.constructor("alert(1)")()}}',  # 模板注入尝试
    '"; alert(1); //',
    "<a href=\"javascript:alert(1)\">click</a>",
]


class TestXSSDefense:
    """验证各输入字段的 XSS 防御。"""

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_document_title_xss(self, client, db, admin_user, payload, tmp_path):
        """资料标题中的 XSS payload 应被转义。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        # 先创建文档，再上传文件到文档（§8：文件通过 POST /documents/{id}/file 上传）
        resp = client.post("/api/v1/documents", json={
            "title": payload,
            "effective_from": "2026-01-01",
        }, headers=headers)
        assert resp.status_code in (200, 201)
        doc_id = resp.json()["id"]

        pdf = make_pdf(tmp_path / "xss_test.pdf")
        resp = client.post(f"/api/v1/documents/{doc_id}/file",
                           files={"file": ("xss_test.pdf", pdf.read_bytes(), "application/pdf")},
                           headers=headers)
        assert resp.status_code == 200

        # 从 API 获取标题，验证返回的是原始文本（不做 HTML 渲染）
        resp = client.get(f"/api/v1/documents/{doc_id}", headers=headers)
        returned_title = resp.json()["title"]
        assert returned_title == payload, "API 应原样返回输入文本"

        # 列表 API 中也应原样返回
        resp = client.get("/api/v1/documents", headers=headers)
        items = resp.json().get("items", [])
        matching = [d for d in items if d["id"] == doc_id]
        assert len(matching) == 1
        assert matching[0]["title"] == payload

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_notice_title_xss(self, client, db, admin_user, normal_user, payload):
        """通知标题中的 XSS payload 应被转义。"""
        from app.models.notice import NoticeType

        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        notice_type = db.query(NoticeType).first()
        type_name = notice_type.name if notice_type else "发布"

        resp = client.post("/api/v1/notices", json={
            "title": payload,
            "content": "正常内容",
            "notice_type": type_name,
            "effective_at": "2026-01-01T00:00:00",
        }, headers=headers)
        assert resp.status_code in (200, 201)

        notice_id = resp.json()["id"]
        resp = client.get(f"/api/v1/notices/{notice_id}", headers=headers)
        assert resp.json()["title"] == payload

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_notice_content_xss(self, client, db, admin_user, normal_user, payload):
        """通知正文中的 XSS payload 应被转义。"""
        from app.models.notice import NoticeType

        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        notice_type = db.query(NoticeType).first()
        type_name = notice_type.name if notice_type else "发布"

        resp = client.post("/api/v1/notices", json={
            "title": "正常标题",
            "content": payload,
            "notice_type": type_name,
            "effective_at": "2026-01-01T00:00:00",
        }, headers=headers)
        assert resp.status_code in (200, 201)

        notice_id = resp.json()["id"]
        resp = client.get(f"/api/v1/notices/{notice_id}", headers=headers)
        assert resp.json()["content"] == payload

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_document_summary_xss(self, client, db, admin_user, payload, tmp_path):
        """资料说明中的 XSS payload 应被转义。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        # 先创建文档，再上传文件到文档（§8：文件通过 POST /documents/{id}/file 上传）
        resp = client.post("/api/v1/documents", json={
            "title": "正常标题",
            "summary": payload,
            "effective_from": "2026-01-01",
        }, headers=headers)
        assert resp.status_code in (200, 201)
        doc_id = resp.json()["id"]

        pdf = make_pdf(tmp_path / "xss_summary_test.pdf")
        resp = client.post(f"/api/v1/documents/{doc_id}/file",
                           files={"file": ("xss_summary_test.pdf", pdf.read_bytes(), "application/pdf")},
                           headers=headers)
        assert resp.status_code == 200

        resp = client.get(f"/api/v1/documents/{doc_id}", headers=headers)
        assert resp.json()["summary"] == payload

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_user_display_name_xss(self, client, db, admin_user, payload):
        """用户显示名中的 XSS payload 不应导致服务端异常。"""
        body = client.post("/api/v1/auth/login",
                           json={"username": "admin", "password": "admin1234"}).json()
        headers = _auth(body["access_token"])

        # 尝试通过用户管理 API 设置 XSS 显示名
        resp = client.post("/api/v1/users", json={
            "username": "xss_user",
            "display_name": payload,
            "password": "user1234",
            "role": "user",
        }, headers=headers)

        # 不管创建是否成功，服务端不应 500
        assert resp.status_code in (200, 201, 400, 409, 422), (
            f"XSS payload 不应导致 500: {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# 安全响应头（2026-10-29 新增）
# ---------------------------------------------------------------------------
class TestSecurityHeaders:
    """基础安全响应头必须存在。

    2026-10-29 审查发现：全仓此前**没有任何**安全响应头，
    导致两个真实可利用面——MIME 嗅探（上传文件被当 HTML 执行）
    与点击劫持（内网其他站点把本系统嵌 iframe 诱导管理员操作）。
    """

    def test_headers_present_on_html_page(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"
        assert resp.headers["referrer-policy"] == "same-origin"

    def test_csp_blocks_inline_script_without_nonce(self, client):
        """CSP 必须禁止任意内联脚本（只放行带 nonce 的）。"""
        resp = client.get("/login")
        csp = resp.headers["content-security-policy"]
        assert "script-src 'self'" in csp
        assert "'nonce-" in csp
        # 不允许无差别放行内联脚本
        assert "'unsafe-inline'" not in csp.split("style-src")[0], (
            "script-src 不得含 'unsafe-inline'"
        )
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp

    def test_csp_frame_src_allows_blob_for_pdf_preview(self, client):
        """PDF 预览的 blob: iframe 必须被放行，但不得连带放行外部站点。

        2026-10-29 公司部署实测缺陷：PDF 预览走 `fetch → blob: → <iframe>`，
        而 CSP 无 `frame-src` 时回退 `default-src 'self'`，`blob:` 不属于
        `'self'` → iframe 被拦，页面显示「已阻止此内容」。
        修复只在 frame-src 放行 `'self'` 与本页 JS 自建的 blob:，
        `frame-ancestors 'none'`（防点击劫持）保持不动。
        """
        csp = client.get("/login").headers["content-security-policy"]
        frame_src = [p.strip() for p in csp.split(";") if p.strip().startswith("frame-src")]
        assert frame_src, "CSP 必须显式声明 frame-src（否则回退 default-src 会拦掉 blob:）"
        directive = frame_src[0]
        assert "blob:" in directive, "frame-src 必须放行 blob:，否则 PDF 预览被拦"
        assert "'self'" in directive, "frame-src 必须保留同源 iframe"
        # 除了 'self' 与 blob:，不得再放行任何来源（不得出现 http/https 或通配）
        assert "http:" not in directive and "https:" not in directive, (
            f"frame-src 不得放行外部站点：{directive}"
        )
        assert "*" not in directive, f"frame-src 不得使用通配：{directive}"
        assert "frame-ancestors 'none'" in csp, "防点击劫持不得因该修复而放宽"

    def test_csp_nonce_is_unique_per_request(self, client):
        """nonce 必须每请求随机（固定 nonce 等于没有防护）。"""
        import re

        n1 = re.search(r"'nonce-([^']+)'",
                       client.get("/login").headers["content-security-policy"]).group(1)
        n2 = re.search(r"'nonce-([^']+)'",
                       client.get("/login").headers["content-security-policy"]).group(1)
        assert n1 != n2, "nonce 每次请求必须不同"

    def test_inline_script_carries_matching_nonce(self, client, admin_headers):
        """模板内联脚本必须带本响应 CSP 里的同一个 nonce，否则会被浏览器拦掉。

        这是最容易出错的地方：漏加 nonce 的模板在浏览器里会静默失效，
        单元测试和 curl 都看不出来，只有真实浏览器才知道 JS 没跑。
        """
        import re

        resp = client.get("/admin/documents/new", headers=admin_headers)
        assert resp.status_code == 200
        nonce = re.search(r"'nonce-([^']+)'",
                          resp.headers["content-security-policy"]).group(1)
        html = resp.text
        inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html)
        assert inline, "该页应至少有一个内联 script"
        for tag in inline:
            # JSON 数据块（type="application/json"）不执行，不需要 nonce
            if 'type="application/json"' in tag:
                continue
            assert f'nonce="{nonce}"' in tag, f"内联脚本缺 nonce：{tag}"

    def test_no_inline_event_handlers_in_templates(self):
        """模板里不得出现 onclick/onchange 等内联事件（CSP 下会被拦）。

        改用 data-action + ui.js 事件委托。
        """
        import pathlib
        import re

        tmpl_dir = pathlib.Path(__file__).resolve().parent.parent / "app" / "web" / "templates"
        pattern = re.compile(r"\bon(click|change|input|submit|load|error|keyup)\s*=", re.I)
        offenders = []
        for f in tmpl_dir.rglob("*.html"):
            text = f.read_text(encoding="utf-8")
            for m in pattern.finditer(text):
                offenders.append(f"{f.name}: {m.group(0)}")
        assert not offenders, f"模板含内联事件（CSP 会拦截）：{offenders}"
