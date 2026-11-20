from httpx import AsyncClient


async def test_health_503_when_db_unreachable(client: AsyncClient) -> None:
    # Test settings point DATABASE_URL at a closed port.
    response = await client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "error"}
