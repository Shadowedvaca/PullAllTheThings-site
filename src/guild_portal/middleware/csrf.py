"""Same-origin protection for unsafe cookie-authenticated requests."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from guild_portal.config import get_settings
from guild_portal.deps import COOKIE_NAME

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _trusted_origin(request: Request) -> str:
    configured = get_settings().app_url.strip()
    if configured:
        return _origin(configured)
    return _origin(str(request.base_url))


class CookieSameOriginMiddleware(BaseHTTPMiddleware):
    """Reject cross-origin unsafe requests that carry the PATT session cookie."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method not in _UNSAFE_METHODS or COOKIE_NAME not in request.cookies:
            return await call_next(request)

        supplied = _origin(request.headers.get("origin", ""))
        if not supplied:
            supplied = _origin(request.headers.get("referer", ""))
        if not supplied or supplied != _trusted_origin(request):
            if request.url.path.startswith("/api/"):
                return Response(
                    content='{"ok":false,"error":"Cross-origin request rejected"}',
                    status_code=403,
                    media_type="application/json",
                )
            return Response("Cross-origin request rejected", status_code=403)
        return await call_next(request)
