"""Double-submit CSRF protection (NFR-SEC-1).

The `csrf_token` cookie is readable by JS (not HttpOnly) and issued on any response that
lacks it. Every POST/PUT/PATCH/DELETE under /api must echo it in `X-CSRF-Token`.
"""

import hmac
from typing import Any

from fastapi import Response
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings
from app.errors import error_response
from app.security.headers import is_api_path
from app.security.tokens import new_token

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _cookie_kwargs(settings: Settings) -> dict[str, Any]:
    return {
        "max_age": settings.session_ttl_days * 86400,
        "path": "/",
        "secure": settings.cookie_secure,
        "httponly": False,
        "samesite": "lax",
    }


def rotate_csrf_cookie(response: Response, settings: Settings) -> str:
    """Issue a fresh CSRF token on `response` (call at login) and return it."""
    token = new_token()
    response.set_cookie(CSRF_COOKIE_NAME, token, **_cookie_kwargs(settings))
    return token


class CSRFMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        cookie = cookie_parser(headers.get("cookie", "")).get(CSRF_COOKIE_NAME)

        if scope["method"] in UNSAFE_METHODS and is_api_path(scope["path"]):
            sent = headers.get(CSRF_HEADER_NAME)
            if not (cookie and sent and hmac.compare_digest(sent.encode(), cookie.encode())):
                response = error_response(
                    403, "csrf_failed", "Your session check failed. Please reload and try again."
                )
                if not cookie:
                    rotate_csrf_cookie(response, self.settings)
                await response(scope, receive, send)
                return

        if cookie:
            await self.app(scope, receive, send)
            return

        issuer = Response()
        rotate_csrf_cookie(issuer, self.settings)
        cookie_header = issuer.headers["set-cookie"]

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                cache_control = out.get("cache-control", "").lower()
                # Publicly cacheable responses (hashed assets) must not carry a per-client
                # cookie: a shared cache could hand one client's token to others.
                shareable = "public" in cache_control or "immutable" in cache_control
                # The route may already have issued one (login rotation); don't duplicate.
                issued = any(
                    v.startswith(CSRF_COOKIE_NAME + "=") for v in out.getlist("set-cookie")
                )
                if not shareable and not issued:
                    out.append("set-cookie", cookie_header)
            await send(message)

        await self.app(scope, receive, send_with_cookie)
