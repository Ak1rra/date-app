"""Security headers (NFR-SEC-1): a strict set for the JSON API, a page-friendly set for the
frontend files this process also serves."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

API_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
}

STATIC_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "content-security-policy": (
        "default-src 'self'; img-src 'self' data: https:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _no_store(path: str) -> bool:
    return (
        path == "/api/v1/auth"
        or path.startswith("/api/v1/auth/")
        or path == "/api/v1/me"
        or path.startswith("/api/v1/me/")
    )


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        api = is_api_path(path)
        static_headers = API_HEADERS if api else STATIC_HEADERS
        no_store = _no_store(path)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in static_headers.items():
                    headers[name] = value
                if no_store:
                    headers["cache-control"] = "no-store"
                elif api and "cache-control" not in headers:
                    # API responses are per-user or unauthenticated-but-sensitive: never cached
                    # unless a route says otherwise.
                    headers["cache-control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)
