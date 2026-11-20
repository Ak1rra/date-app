from httpx import AsyncClient

from tests.conftest import ORIGIN


async def test_foreign_origin_gets_no_cors_headers(client: AsyncClient) -> None:
    response = await client.get("/api/v1/meta", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


async def test_foreign_origin_preflight_rejected(client: AsyncClient) -> None:
    response = await client.options(
        "/api/v1/_test/echo",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-csrf-token",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


async def test_app_origin_allowed_with_credentials(client: AsyncClient) -> None:
    response = await client.get("/api/v1/meta", headers={"Origin": ORIGIN})
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


async def test_app_origin_preflight_allows_csrf_header(client: AsyncClient) -> None:
    response = await client.options(
        "/api/v1/_test/echo",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-csrf-token, content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "x-csrf-token" in response.headers["access-control-allow-headers"].lower()
