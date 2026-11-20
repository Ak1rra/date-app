from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from tests.conftest import make_settings

IMMUTABLE = "public, max-age=31536000, immutable"


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "icons").mkdir()
    (root / "index.html").write_text("<!doctype html><title>spa</title>")
    (root / "sw.js").write_text("// sw")
    (root / "registerSW.js").write_text("// register")
    (root / "manifest.webmanifest").write_text('{"name": "Date Planner"}')
    (root / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    (root / "workbox-abc123.js").write_text("// workbox")
    (root / "assets" / "app-abc123.js").write_text("console.log(1)")
    (root / "assets" / "app-abc123.css").write_text("body{}")
    (root / "icons" / "icon-192.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "secret.txt").write_text("top secret")  # outside dist
    return root


@pytest.fixture
def web_app(dist: Path) -> FastAPI:
    return create_app(make_settings(frontend_dist=dist))


@pytest.fixture
async def web(web_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=web_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def raw_get(app: FastAPI, path: str) -> tuple[int, bytes]:
    """GET with a literal scope path, bypassing any client-side URL normalisation."""
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)  # type: ignore[arg-type]
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


# --- cache rules


async def test_assets_are_immutable(web: AsyncClient) -> None:
    for name in ("app-abc123.js", "app-abc123.css"):
        response = await web.get(f"/assets/{name}")
        assert response.status_code == 200
        assert response.headers["cache-control"] == IMMUTABLE


@pytest.mark.parametrize(
    "path", ["/index.html", "/sw.js", "/registerSW.js", "/manifest.webmanifest"]
)
async def test_entry_files_are_no_cache(web: AsyncClient, path: str) -> None:
    response = await web.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


async def test_other_files_served_normally(web: AsyncClient) -> None:
    for path, body in (
        ("/favicon.ico", b"\x00\x00\x01\x00"),
        ("/workbox-abc123.js", b"// workbox"),
        ("/icons/icon-192.png", b"\x89PNG\r\n"),
    ):
        response = await web.get(path)
        assert response.status_code == 200
        assert response.content == body
        assert "cache-control" not in response.headers


async def test_root_and_spa_fallback_are_no_cache_index(web: AsyncClient) -> None:
    for path in ("/", "/us", "/ideas/deep/link", "/auth/verify", "/assets", "/icons/"):
        response = await web.get(path)
        assert response.status_code == 200, path
        assert "<title>spa</title>" in response.text
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["content-type"].startswith("text/html")


async def test_head_request(web: AsyncClient) -> None:
    response = await web.head("/us")
    assert response.status_code == 200
    assert response.content == b""


async def test_missing_asset_is_404_not_index(web: AsyncClient) -> None:
    response = await web.get("/assets/old-hash.js")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_api_404_stays_json(web: AsyncClient) -> None:
    for path in ("/api/nope", "/api/v1/nope", "/api"):
        response = await web.get(path)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "not_found"


async def test_api_still_works_and_gets_csrf_cookie_on_index(web: AsyncClient) -> None:
    assert (await web.get("/api/v1/meta")).status_code == 200
    web.cookies.clear()
    index = await web.get("/")
    assert index.headers["set-cookie"].startswith("csrf_token=")


async def test_non_get_outside_api_is_not_the_spa(web: AsyncClient) -> None:
    response = await web.post("/us")
    assert response.status_code in (404, 405)
    assert response.headers["content-type"].startswith("application/json")


# --- content types


async def test_content_types(web: AsyncClient) -> None:
    expected = {
        "/manifest.webmanifest": "application/manifest+json",
        "/sw.js": "text/javascript",
        "/assets/app-abc123.js": "text/javascript",
        "/assets/app-abc123.css": "text/css",
        "/icons/icon-192.png": "image/png",
    }
    for path, media_type in expected.items():
        response = await web.get(path)
        assert response.headers["content-type"].split(";")[0] == media_type, path


# --- traversal


@pytest.mark.parametrize(
    "path",
    [
        "/../secret.txt",
        "/assets/../../secret.txt",
        "/%2e%2e/secret.txt",
        "/%2E%2E/secret.txt",
        "/..%2fsecret.txt",
        "/assets/..%2f..%2fsecret.txt",
        "/.env",
        "/..\\secret.txt",
        "//etc/passwd",
    ],
)
async def test_traversal_is_not_served(web: AsyncClient, path: str) -> None:
    response = await web.get(path)
    assert b"top secret" not in response.content
    assert response.status_code in (200, 404)
    if response.status_code == 200:  # e.g. "//etc/passwd" is just an unknown page route
        assert "<title>spa</title>" in response.text


@pytest.mark.parametrize(
    "path", ["/../secret.txt", "/assets/../../secret.txt", "/.env", "/..\\secret.txt"]
)
async def test_traversal_literal_paths_are_404(web_app: FastAPI, path: str) -> None:
    status, body = await raw_get(web_app, path)
    assert status == 404
    assert b"top secret" not in body
    assert b'"not_found"' in body


async def test_encoded_dotdot_is_404(web: AsyncClient) -> None:
    for path in ("/%2e%2e/secret.txt", "/..%2fsecret.txt"):
        response = await web.get(path)
        assert response.status_code == 404, path


# --- headers


async def test_header_sets_differ_by_path(web: AsyncClient) -> None:
    page = (await web.get("/us")).headers
    asset = (await web.get("/assets/app-abc123.js")).headers
    api = (await web.get("/api/v1/meta")).headers
    csp = (
        "default-src 'self'; img-src 'self' data: https:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    for headers in (page, asset):
        assert headers["content-security-policy"] == csp
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
    assert api["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert api["x-frame-options"] == "DENY"


# --- no dist


async def test_no_dist_api_fine_and_root_is_503_envelope(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/meta")).status_code == 200
    response = await client.get("/")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "frontend_unavailable"
    assert (await client.get("/us")).status_code == 503
    assert (await client.get("/api/v1/nope")).status_code == 404
    assert "content-security-policy" in response.headers


# --- index.html symlink escaping dist


async def test_index_symlink_outside_dist_is_503(tmp_path: Path) -> None:
    outside = tmp_path / "outside.html"
    outside.write_text("<title>OUTSIDE-SECRET</title>")
    dist = tmp_path / "dist"
    dist.mkdir()
    try:
        (dist / "index.html").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not permitted on this platform")
    app = create_app(make_settings(frontend_dist=dist))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        for path in ("/", "/us", "/index.html"):
            response = await c.get(path)
            assert response.status_code == 503, path
            assert response.json()["error"]["code"] == "frontend_unavailable"
            assert "OUTSIDE-SECRET" not in response.text


# --- CSRF cookie vs shared caches


async def test_no_csrf_cookie_on_immutable_assets_but_on_pages(web: AsyncClient) -> None:
    asset = await web.get("/assets/app-abc123.js")
    assert asset.status_code == 200
    assert "set-cookie" not in asset.headers
    web.cookies.clear()
    page = await web.get("/")
    assert page.headers["set-cookie"].startswith("csrf_token=")
