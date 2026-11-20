from httpx import AsyncClient


async def test_404_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


async def test_405_envelope(csrf_client: AsyncClient) -> None:
    response = await csrf_client.post("/api/v1/meta")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


async def test_422_envelope_with_fields(csrf_client: AsyncClient) -> None:
    response = await csrf_client.post("/api/v1/_test/validate", json={"name": "x", "count": "abc"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"]
    assert error["fields"][0]["loc"] == ["body", "count"]
    assert "input" not in error["fields"][0]


async def test_api_error_with_headers(client: AsyncClient) -> None:
    response = await client.get("/api/v1/_test/limited")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "7"
    assert response.json() == {"error": {"code": "rate_limited", "message": "Slow down."}}


async def test_current_user_placeholder_is_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/_test/private")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_unhandled_error_is_500_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/_test/boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "kaboom" not in response.text


async def test_fastapi_http_exception_uses_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/_test/teapot")
    assert response.status_code == 418
    assert response.json() == {"error": {"code": "http_error", "message": "x"}}
