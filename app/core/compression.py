"""Selective gzip middleware.

Compress text-like application responses while leaving binary downloads and
previews untouched. The built-in Starlette GZipMiddleware compresses every
content type except text/event-stream; that is unsafe for PDF/Office/image file
responses, where compression adds CPU cost and can interfere with range/stream
delivery.
"""
from __future__ import annotations

from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware, GZipResponder, IdentityResponder
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_COMPRESSIBLE_TYPES = (
    "application/json",
    "application/javascript",
    "application/xml",
    "image/svg+xml",
)


def _is_compressible(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type.startswith("text/") or media_type in _COMPRESSIBLE_TYPES


def _accepts_gzip(accept_encoding: str) -> bool:
    """RFC 7231: gzip;q=0 表示明确拒绝，xgzip 不能误判为 gzip。"""
    if not accept_encoding:
        return False
    for item in accept_encoding.lower().split(","):
        parts = item.strip().split(";")
        if parts[0].strip() != "gzip":
            continue
        q = 1.0
        for param in parts[1:]:
            name, _, value = param.strip().partition("=")
            if name.strip() == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        if q > 0:
            return True
    return False

class SelectiveGZipResponder(GZipResponder):
    async def send_with_compression(self, message: Message) -> None:
        await super().send_with_compression(message)
        if message["type"] == "http.response.start":
            headers = Headers(raw=message["headers"])
            if not _is_compressible(headers.get("content-type", "")):
                self.content_type_is_excluded = True


class SelectiveGZipMiddleware(GZipMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        minimum_size: int = 1024,
        compresslevel: int = 6,
    ) -> None:
        super().__init__(app, minimum_size=minimum_size, compresslevel=compresslevel)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        responder: ASGIApp
        if _accepts_gzip(headers.get("accept-encoding", "")):
            responder = SelectiveGZipResponder(
                self.app,
                self.minimum_size,
                compresslevel=self.compresslevel,
            )
        else:
            responder = IdentityResponder(self.app, self.minimum_size)

        await responder(scope, receive, send)
