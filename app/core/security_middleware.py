"""轻量安全中间件：CSRF Origin 校验 + 登录限速。

- CSRF：非 GET 请求校验 Origin/Referer 是否匹配本域（防跨站伪造）。
- 限速：基于 IP 的内存计数器（无需外部依赖），登录窗口内超过阈值则 429。
"""
from __future__ import annotations

import secrets
import time
from collections import defaultdict
from typing import Optional
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.errors import AppError

# ---------------------------------------------------------------------------
# CSRF Origin 校验
# ---------------------------------------------------------------------------

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# 不需要 CSRF 校验的路径前缀（如 API 登录/登出——没有会话状态可伪造）
CSRF_EXEMPT_PREFIXES = ("/api/v1/auth/login", "/api/v1/auth/logout", "/health", "/static")

_LOGIN_RETURN_PATHS = frozenset({"/documents", "/notices"})


def _normalize_host(value: str | None) -> str:
    return (value or "").strip().lower().rstrip(".")


def _parse_authority(value: str | None) -> tuple[str, Optional[int]] | None:
    """解析 Host / X-Forwarded-Host，不接受路径、查询、用户信息或逗号列表。"""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit("//" + raw)
        port = parsed.port
    except ValueError:
        return None
    if (
        not parsed.hostname
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return None
    return _normalize_host(parsed.hostname), port


def _host_is_trusted(host: str) -> bool:
    """Host 只能命中显式配置白名单；请求头本身不能成为可信来源。"""
    raw = getattr(get_settings(), "TRUSTED_HOSTS", "")
    for candidate in (part.strip().lower().rstrip(".") for part in raw.split(",")):
        if not candidate or candidate == "*":
            continue
        if candidate.startswith("*."):
            suffix = candidate[1:]
            if host.endswith(suffix) and host != candidate[2:]:
                return True
        elif host == candidate:
            return True
    return False


def _default_port(scheme: str) -> Optional[int]:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None


def _expected_authority(request: Request) -> tuple[str, str, int]:
    """返回可信的 (scheme, host, port)。

    - 默认只使用请求 Host，且该 Host 必须在 TRUSTED_HOSTS 中；
    - 仅当 TRUST_REVERSE_PROXY_HEADERS=true 时才采用转发头；
    - 无论来源是 Host 还是转发头，都必须再次通过白名单。
    """
    settings = get_settings()
    trust_proxy = bool(settings.TRUST_REVERSE_PROXY_HEADERS)
    authority = request.headers.get("host")
    scheme = request.url.scheme.lower()

    if trust_proxy:
        forwarded_host = request.headers.get("x-forwarded-host")
        if forwarded_host:
            authority = forwarded_host.split(",", 1)[0].strip()
        forwarded_proto = request.headers.get("x-forwarded-proto")
        if forwarded_proto:
            scheme = forwarded_proto.split(",", 1)[0].strip().lower()

    parsed = _parse_authority(authority)
    if parsed is None:
        raise AppError("FORBIDDEN", "CSRF 校验失败：Host 无法解析")
    host, port = parsed
    if not _host_is_trusted(host):
        raise AppError("FORBIDDEN", "CSRF 校验失败：Host 不在可信白名单")
    port = port if port is not None else _default_port(scheme)
    if port is None:
        raise AppError("FORBIDDEN", "CSRF 校验失败：请求协议不受支持")
    return scheme, host, port


def _parse_origin_like(raw: str, *, allow_path: bool) -> tuple[str, str, int]:
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise AppError("FORBIDDEN", "CSRF 校验失败：Origin 无法解析") from exc
    scheme = parsed.scheme.lower()
    host = _normalize_host(parsed.hostname)
    if (
        scheme not in {"http", "https"}
        or not host
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise AppError("FORBIDDEN", "CSRF 校验失败：Origin 无法解析")
    if not allow_path and (parsed.path not in {"", "/"} or parsed.query):
        raise AppError("FORBIDDEN", "CSRF 校验失败：Origin 含非法路径或多值")
    port = port if port is not None else _default_port(scheme)
    if port is None:
        raise AppError("FORBIDDEN", "CSRF 校验失败：Origin 协议不受支持")
    return scheme, host, port


def _check_origin(request: Request) -> None:
    """校验 Origin / Referer 是否为配置白名单内的同源请求。

    关键边界：不再把请求自带 Host 直接当作可信来源，也取消 localhost 任意端口例外。
    """
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    raw = origin or referer
    if not raw:
        # 无 Origin/Referer 的脚本/API 调用保持既有兼容性。
        return

    expected_scheme, expected_host, expected_port = _expected_authority(request)
    remote_scheme, remote_host, remote_port = _parse_origin_like(
        raw,
        allow_path=not bool(origin),
    )
    if (
        remote_scheme == expected_scheme
        and remote_host == expected_host
        and remote_port == expected_port
    ):
        return
    raise AppError("FORBIDDEN", "CSRF 校验失败：跨域请求被拒绝")


def _safe_login_next(raw: str | None) -> str | None:
    """登录后只允许回资料/通知列表；其余值由服务端剥除。"""
    if not raw or len(raw) > 2048:
        return None
    if not raw.startswith("/") or raw.startswith("//"):
        return None
    if "\\" in raw or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        return None
    try:
        target = urlsplit(raw)
    except ValueError:
        return None
    if target.scheme or target.netloc or target.fragment:
        return None
    if target.path not in _LOGIN_RETURN_PATHS:
        return None
    return target.path + (("?" + target.query) if target.query else "")


# ---------------------------------------------------------------------------
# 登录限速（内存计数器）
# ---------------------------------------------------------------------------


class _LoginRateLimiter:
    """每 IP 维护一个滑动窗口（列表），超阈值则拒绝。

    H2: 窗口/阈值来自配置（Settings），禁止硬编码可调参数。
    """

    def __init__(self, window: Optional[int] = None, max_attempts: Optional[int] = None) -> None:
        settings = get_settings()
        self._window = window if window is not None else settings.LOGIN_RATE_WINDOW
        self._max = max_attempts if max_attempts is not None else settings.LOGIN_RATE_MAX_ATTEMPTS
        self._buckets: dict[str, list[float]] = defaultdict(list)
        import threading
        self._lock = threading.Lock()

    def check(self, ip: Optional[str]) -> None:
        if ip is None:
            return
        now = time.monotonic()
        with self._lock:
            ts = self._buckets[ip]
            # 淘汰过期
            cutoff = now - self._window
            ts[:] = [t for t in ts if t > cutoff]
            if len(ts) >= self._max:
                retry_after = int(ts[0] - cutoff) + 1
                raise AppError(
                    "RATE_LIMITED",
                    f"登录尝试过于频繁，请 {retry_after} 秒后重试",
                )

    def record(self, ip: Optional[str]) -> None:
        """记录一次登录尝试（无论成功）。"""
        if ip is None:
            return
        with self._lock:
            self._buckets[ip].append(time.monotonic())


login_rate_limiter = _LoginRateLimiter()


def reset_login_rate_limiter() -> None:
    """清空限速状态（测试隔离用；生产不调用）。"""
    with login_rate_limiter._lock:
        login_rate_limiter._buckets.clear()


# ---------------------------------------------------------------------------
# FastAPI 中间件
# ---------------------------------------------------------------------------

class SecurityMiddleware(BaseHTTPMiddleware):
    """统一安全中间件：CSRF + 安全响应头（含 CSP nonce）。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        method = request.method.upper()

        # CSP nonce：每次请求生成一次，模板里的内联 <script> 需带上它。
        # 这样既能保持 script-src 不含 'unsafe-inline'，又能让模板注入的
        # 少量内联常量（如 window.DOC_ID）正常执行。
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce

        # 登录 next 服务端 allowlist：危险值直接剥除，避免把歧义交给浏览器 URL 归一化。
        if method in {"GET", "HEAD"} and request.url.path == "/login":
            next_values = request.query_params.getlist("next")
            if next_values and (
                len(next_values) != 1 or _safe_login_next(next_values[0]) is None
            ):
                redirect_response = RedirectResponse("/login", status_code=302)
                _apply_security_headers(request, redirect_response, nonce)
                return redirect_response

        # CSRF 校验（非安全方法 + 非豁免路径）
        if method not in SAFE_METHODS:
            path = request.url.path
            if not any(path.startswith(p) for p in CSRF_EXEMPT_PREFIXES):
                try:
                    _check_origin(request)
                except AppError as e:
                    from app.core.errors import error_payload

                    error_response = JSONResponse(
                        status_code=e.status_code,
                        content=error_payload(e.code, e.message, e.detail),
                    )
                    _apply_security_headers(request, error_response, nonce)
                    return error_response

        response = await call_next(request)
        _apply_security_headers(request, response, nonce)
        return response


# 内网系统：无第三方资源、无 CDN，JS 全部在 app/web/static/ 下。
# CSP 采用「默认同源 + nonce 白名单」策略，挡住 XSS 放大面：
#   - script-src 'self' 'nonce-...'  不允许任意 inline script，只放行带 nonce 的
#   - style-src 'self' 'unsafe-inline'  模板/JS 有少量内联 style（主题变量）
#   - img-src 'self' data: blob:     允许 data: 图标；blob: 供图片预览（fetch → blob）
#   - frame-src 'self' blob:         PDF 预览：同源 iframe + 本页 JS 自建的 blob:
#                                    （不声明时回退 default-src，blob: 会被拦成
#                                     「已阻止此内容」；不放行任何外部站点）
#   - frame-ancestors 'none'         等价 X-Frame-Options DENY，防点击劫持
#   - form-action / base-uri 'self'、object-src 'none'  防表单劫持与 base 注入
def _build_csp(nonce: str) -> str:
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-src 'self' blob:; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "base-uri 'self'; "
        "object-src 'none'"
    )


def _apply_security_headers(request: Request, response: Response, nonce: str) -> None:
    """补齐基础安全响应头（2026-10-29 新增）。

    此前全仓没有任何安全响应头，导致两个真实可利用面：
    - 缺 `X-Content-Type-Options: nosniff`：浏览器可能把返回的文件字节
      「嗅探」成 HTML 执行（上传的 PDF/文本预览是最典型入口）；
    - 缺 `X-Frame-Options` / `frame-ancestors`：内网其他站点可把本系统嵌进
      iframe 做点击劫持（诱导管理员点删除/发布）。
    """
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "same-origin")
    headers.setdefault("Content-Security-Policy", _build_csp(nonce))

    # 业务文件预览/下载不进共享缓存（内网共用终端时的残留风险）
    path = request.url.path
    if path.startswith("/api/v1/documents/") or path.startswith("/api/v1/notices/"):
        if (
            "preview" in path
            or path.endswith("/file")
            or path.endswith("/download")
            or path.endswith("/attachment")
        ):
            headers.setdefault("Cache-Control", "private, no-store")
