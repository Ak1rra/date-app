import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from tests.conftest import make_settings

ECHO = "/api/v1/_test/echo"


async def test_cookie_issued_when_absent(client: AsyncClient) -> None:
    response = await client.get("/api/v1/meta")
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("csrf_token=")
    assert "samesite=lax" in cookie.lower()
    assert "httponly" not in cookie.lower()
    assert "secure" not in cookie.lower()  # cookie_secure=False in test settings
    assert "csrf_token" in client.cookies


async def test_cookie_not_reissued_when_present(csrf_client: AsyncClient) -> None:
    response = await csrf_client.get("/api/v1/meta")
    assert "set-cookie" not in response.headers


async def test_secure_flag_follows_config() -> None:
    transport = ASGITransport(app=create_app(make_settings(cookie_secure=True)))
    async with AsyncClient(transport=transport, base_url="https://testserver") as c:
        response = await c.get("/api/v1/meta")
    assert "secure" in response.headers["set-cookie"].lower()


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_unsafe_without_header_is_403(csrf_client: AsyncClient, method: str) -> None:
    del csrf_client.headers["X-CSRF-Token"]
    response = await csrf_client.request(method, ECHO)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


async def test_mismatched_header_is_403(csrf_client: AsyncClient) -> None:
    response = await csrf_client.post(ECHO, headers={"X-CSRF-Token": "not-the-cookie"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


async def test_no_cookie_at_all_is_403_and_issues_one(client: AsyncClient) -> None:
    response = await client.post(ECHO, headers={"X-CSRF-Token": "anything"})
    assert response.status_code == 403
    assert response.headers["set-cookie"].startswith("csrf_token=")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_matching_header_passes(csrf_client: AsyncClient, method: str) -> None:
    response = await csrf_client.request(method, ECHO)
    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_get_is_exempt(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/meta")).status_code == 200


async def test_403_carries_security_headers_and_envelope_shape(client: AsyncClient) -> None:
    response = await client.post(ECHO)
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["/api/other", "/api", "/api/v2/thing"])
async def test_unsafe_under_any_api_path_needs_token(client: AsyncClient, path: str) -> None:
    response = await client.post(path)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"
