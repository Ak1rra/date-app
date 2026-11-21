import json
import re
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.errors import ApiError
from app.providers.email.base import EmailDeliveryError
from app.providers.email.console import OUTBOX, ConsoleEmailSender
from app.providers.email.resend import RESEND_URL, ResendEmailSender
from app.providers.geocoding.fake import DEMO_LAT, DEMO_LNG, MAX_OFFSET, FakeGeocoder
from app.providers.registry import get_email_sender, get_geocoder
from tests.conftest import make_settings

CURRENT: Any = SimpleNamespace(couple_id=None)


async def test_fake_geocoder_is_deterministic() -> None:
    geocoder = FakeGeocoder()
    a = await geocoder.geocode("  east   austin ")
    b = await geocoder.geocode("East Austin")
    assert (a.lat, a.lng, a.place_id) == (b.lat, b.lng, b.place_id)
    assert a.place_id is not None
    assert abs(a.lat - DEMO_LAT) <= MAX_OFFSET + 1e-6
    assert abs(a.lng - DEMO_LNG) <= MAX_OFFSET + 1e-6


QUERIES = ["123 Main St, Austin", "Main St", "Oak Road", "east austin", "Austin, TX 78701"]


@pytest.mark.parametrize("query", QUERIES)
async def test_fake_geocoder_label_is_a_numbered_demo_area(query: str) -> None:
    label = (await FakeGeocoder().geocode(query)).label
    assert re.fullmatch(r"Demo area \d{1,2}", label)
    assert 1 <= int(label.removeprefix("Demo area ")) <= 99


@pytest.mark.parametrize("query", QUERIES)
async def test_fake_geocoder_label_is_deterministic(query: str) -> None:
    first = await FakeGeocoder().geocode(query)
    again = await FakeGeocoder().geocode("  " + query.replace(" ", "   ") + " ")
    assert again.label == first.label
    assert (again.lat, again.lng, again.place_id) == (first.lat, first.lng, first.place_id)


@pytest.mark.parametrize("query", ["123 Main St, Austin", "Main St", "Oak Road"])
async def test_fake_geocoder_label_contains_no_word_from_the_query(query: str) -> None:
    label = (await FakeGeocoder().geocode(query)).label.lower()
    for word in query.lower().replace(",", " ").split():
        assert word not in label
    assert "Main" not in label and "Oak" not in label


async def test_fake_geocoder_differs_by_query() -> None:
    geocoder = FakeGeocoder()
    a = await geocoder.geocode("downtown")
    b = await geocoder.geocode("the domain")
    assert (a.lat, a.lng) != (b.lat, b.lng)


async def test_console_sender_records_link() -> None:
    await ConsoleEmailSender().send("a@example.com", "Hi", "text", None, link="http://x/y")
    entry = OUTBOX[-1]
    assert entry["to"] == "a@example.com"
    assert entry["link"] == "http://x/y"
    assert entry["sent_at"].tzinfo is not None


def test_demo_mode_forces_console_and_fake() -> None:
    settings = make_settings(demo_mode=True, email_provider="resend", email_api_key="k")
    assert isinstance(get_email_sender(settings), ConsoleEmailSender)
    assert isinstance(get_geocoder(settings, CURRENT), FakeGeocoder)


def test_non_demo_geocoder_unavailable() -> None:
    with pytest.raises(ApiError) as info:
        get_geocoder(make_settings(demo_mode=False), CURRENT)
    assert info.value.status == 503
    assert info.value.code == "provider_unavailable"
    assert info.value.message == "Location lookup isn't available right now."


def test_non_demo_resend_needs_credentials() -> None:
    with pytest.raises(ApiError):
        get_email_sender(make_settings(demo_mode=False, email_provider="resend"))
    sender = get_email_sender(
        make_settings(
            demo_mode=False, email_provider="resend", email_api_key="k", email_from="a@b.co"
        )
    )
    assert isinstance(sender, ResendEmailSender)


async def test_dependency_override(app: FastAPI, client: AsyncClient) -> None:
    class Spy:
        async def send(self, *args: object, **kwargs: object) -> None: ...

    spy = Spy()
    app.dependency_overrides[get_email_sender] = lambda: spy
    assert app.dependency_overrides[get_email_sender]() is spy


async def test_resend_posts_expected_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = ResendEmailSender("secret", "Dates <hi@example.com>", client)
        await sender.send("a@example.com", "Hi", "plain", "<p>html</p>", link="http://ignored")
    (request,) = seen
    assert str(request.url) == RESEND_URL
    assert request.headers["authorization"] == "Bearer secret"
    assert json.loads(request.content) == {
        "from": "Dates <hi@example.com>",
        "to": ["a@example.com"],
        "subject": "Hi",
        "text": "plain",
        "html": "<p>html</p>",
    }


async def test_resend_failure_raises_without_leaking_body() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="secret-magic-link-body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = ResendEmailSender("k", "a@b.co", client)
        with pytest.raises(EmailDeliveryError) as info:
            await sender.send("a@example.com", "Hi", "plain")
    assert "secret-magic-link-body" not in str(info.value)
