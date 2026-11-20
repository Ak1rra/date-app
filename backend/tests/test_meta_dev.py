from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app import __version__
from app.main import create_app
from app.providers.email.console import OUTBOX, ConsoleEmailSender
from tests.conftest import make_settings


async def test_meta(client: AsyncClient) -> None:
    response = await client.get("/api/v1/meta")
    assert response.status_code == 200
    assert response.json() == {"demo_mode": True, "version": __version__}


@pytest.fixture
async def dev_client() -> AsyncIterator[AsyncClient]:
    app = create_app(make_settings(demo_mode=True, dev_outbox=True))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


async def test_outbox_empty_when_enabled(dev_client: AsyncClient) -> None:
    response = await dev_client.get("/api/v1/dev/outbox")
    assert response.status_code == 200
    assert response.json() == []


async def test_outbox_404_in_demo_mode_without_dev_outbox(client: AsyncClient) -> None:
    # The default test settings have demo_mode=True and dev_outbox=False.
    await ConsoleEmailSender().send("a@example.com", "s", "body", link="http://x/secret")
    response = await client.get("/api/v1/dev/outbox")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "secret" not in response.text


async def test_outbox_newest_first_and_bounded(dev_client: AsyncClient) -> None:
    sender = ConsoleEmailSender()
    for i in range(60):
        await sender.send(f"u{i}@example.com", f"s{i}", "body", link=f"http://x/{i}")
    assert len(OUTBOX) == 50
    entries = (await dev_client.get("/api/v1/dev/outbox")).json()
    assert len(entries) == 50
    assert entries[0]["to"] == "u59@example.com"
    assert entries[0]["link"] == "http://x/59"
    assert entries[-1]["to"] == "u10@example.com"
    assert set(entries[0]) == {"to", "subject", "link", "sent_at"}


async def test_outbox_404_when_dev_outbox_off_and_not_demo() -> None:
    app = create_app(make_settings(demo_mode=False, dev_outbox=False))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        response = await c.get("/api/v1/dev/outbox")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
