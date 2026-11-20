"""Serves the built frontend (frontend/dist) from this process, replacing a separate web server.

Rules (the same ones the old nginx config had):
- /assets/*                      -> the file, cached for a year (Vite hashes the names)
- index.html, sw.js, registerSW.js, manifest.webmanifest -> the file, never cached
- any other existing file in dist -> the file
- any other GET/HEAD outside /api -> index.html (SPA fallback, never cached)
- anything under /api, other methods, traversal attempts, missing /assets files
                                 -> not handled here, so the app answers its JSON 404
"""

import logging
import mimetypes
from pathlib import Path

from starlette.responses import FileResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings
from app.errors import error_response
from app.security.headers import is_api_path

logger = logging.getLogger(__name__)

IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"
NO_CACHE_FILES = frozenset({"index.html", "sw.js", "registerSW.js", "manifest.webmanifest"})
MEDIA_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".webmanifest": "application/manifest+json",
    ".css": "text/css",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".map": "application/json",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".woff2": "font/woff2",
}


def _media_type(path: Path) -> str | None:
    return MEDIA_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]


def check_frontend(settings: Settings) -> None:
    dist = settings.resolved_frontend_dist
    if FrontendMiddleware._existing_file(dist.resolve(), "index.html") is None:
        logger.warning(
            "frontend not built: no index.html in %s; the API still works. "
            "Build it or set FRONTEND_DIST.",
            dist,
        )


class FrontendMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.dist = settings.resolved_frontend_dist

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in ("GET", "HEAD")
            or is_api_path(scope["path"])
        ):
            await self.app(scope, receive, send)
            return

        root = self.dist.resolve()
        index = self._existing_file(root, "index.html")
        if index is None:
            response: Response | None = error_response(
                503, "frontend_unavailable", "The web app hasn't been built yet."
            )
        else:
            response = self._respond(root, index, scope["path"])
        if response is None:
            await self.app(scope, receive, send)  # the JSON 404 envelope
        else:
            await response(scope, receive, send)

    def _respond(self, root: Path, index: Path, path: str) -> Response | None:
        """`index` is index.html, already resolved and checked to lie inside `root`."""
        rel = path.lstrip("/")
        if not rel:
            return self._file(index, NO_CACHE)
        parts = rel.split("/")
        if any(p.startswith(".") or "\\" in p or "\0" in p for p in parts):
            return None  # dotfiles, "..", backslash tricks: never served, never the SPA
        candidate = self._existing_file(root, rel)
        if rel.startswith("assets/"):
            return self._file(candidate, IMMUTABLE) if candidate else None
        if candidate is None:
            return self._file(index, NO_CACHE)
        return self._file(candidate, NO_CACHE if rel in NO_CACHE_FILES else None)

    @staticmethod
    def _existing_file(root: Path, rel: str) -> Path | None:
        try:
            candidate = (root / rel).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return candidate
        except (OSError, ValueError):
            pass
        return None

    @staticmethod
    def _file(path: Path, cache_control: str | None) -> Response:
        headers = {"cache-control": cache_control} if cache_control else None
        return FileResponse(path, media_type=_media_type(path), headers=headers)
