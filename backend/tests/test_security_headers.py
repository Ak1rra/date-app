from httpx import ASGITransport, AsyncClient

from app.main import create_app
from tests.conftest import make_settings


async def test_static_headers_present(client: AsyncClient) -> None:
    headers = (await client.get("/api/v1/meta")).headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-frame-options"] == "DENY"
    assert headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"


async def test_headers_present_on_errors(client: AsyncClient) -> None:
    headers = (await client.get("/api/v1/nope")).headers
    assert headers["x-content-type-options"] == "nosniff"


async def test_no_store_on_auth_and_me(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/me")).headers["cache-control"] == "no-store"
    assert (await client.get("/api/v1/auth/probe")).headers["cache-control"] == "no-store"
    # Even a 404 under /auth/ must not be cached.
    assert (await client.get("/api/v1/auth/missing")).headers["cache-control"] == "no-store"


async def test_api_responses_default_to_no_store(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/health")).headers["cache-control"] == "no-store"
    assert (await client.get("/api/v1/meta")).headers["cache-control"] == "no-store"
    assert (await client.get("/api/v1/nope")).headers["cache-control"] == "no-store"


async def test_dev_outbox_is_no_store() -> None:
    app = create_app(make_settings(demo_mode=True, dev_outbox=True))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        response = await c.get("/api/v1/dev/outbox")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


async def test_request_id_header(client: AsyncClient) -> None:
    generated = (await client.get("/api/v1/meta")).headers["x-request-id"]
    assert len(generated) == 32
    echoed = await client.get("/api/v1/meta", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123"
    junk = await client.get("/api/v1/meta", headers={"X-Request-ID": "bad id!\t"})
    assert junk.headers["x-request-id"] != "bad id!\t"
