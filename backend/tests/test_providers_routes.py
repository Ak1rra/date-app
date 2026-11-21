"""GoogleRoutes (httpx.MockTransport, no network) and FakeRoutes."""

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.providers.cache import RunMemo
from app.providers.http import Runtime
from app.providers.routes.fake import FakeRoutes
from app.providers.routes.google import COMPUTE_URL, FIELD_MASK, GoogleRoutes
from app.providers.types import LatLng, ProviderUnavailable
from tests.provider_helpers import ok_json

KEY = "routes-secret"
A = LatLng(47.6, -122.33)
B = LatLng(47.62, -122.35)
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
FUTURE = datetime(2026, 11, 20, 19, 7, tzinfo=UTC)
PAST = datetime(2026, 11, 19, 19, 0, tzinfo=UTC)


def make(handler: Any, **kwargs: Any) -> GoogleRoutes:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GoogleRoutes(KEY, client=client, now=NOW, **kwargs)


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"routes": [{"duration": "165s", "distanceMeters": 772}]})


async def test_drive_request_and_response() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok(request)

    leg = await make(handler).travel(A, B, "drive", FUTURE)
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == COMPUTE_URL
    assert request.headers["x-goog-api-key"] == KEY
    assert (
        request.headers["x-goog-fieldmask"] == FIELD_MASK == "routes.duration,routes.distanceMeters"
    )
    body = json.loads(request.content)
    assert body["origin"]["location"]["latLng"] == {"latitude": 47.6, "longitude": -122.33}
    assert body["destination"]["location"]["latLng"] == {"latitude": 47.62, "longitude": -122.35}
    assert body["travelMode"] == "DRIVE"
    assert body["routingPreference"] == "TRAFFIC_AWARE"
    assert body["departureTime"] == "2026-11-20T19:00:00Z"  # 19:07 bucketed to 15 minutes
    assert (leg.mode, leg.duration_minutes, leg.distance_m) == ("drive", 3, 772)  # 165 s rounds up


async def test_drive_and_rideshare_are_not_mixed_up_by_the_run_memo() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return ok(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    routes = GoogleRoutes(KEY, client=client, now=NOW, runtime=Runtime(memo=RunMemo()))
    drive = await routes.travel(A, B, "drive", FUTURE)
    rideshare = await routes.travel(A, B, "rideshare", FUTURE)
    assert (drive.mode, rideshare.mode) == ("drive", "rideshare")  # the requested mode, each time
    assert len(calls) == 2  # same Google request, separate memo entries
    assert (await routes.travel(A, B, "rideshare", FUTURE)).mode == "rideshare"
    assert (await routes.travel(A, B, "drive", FUTURE)).mode == "drive"
    assert len(calls) == 2


async def test_modes_map_to_google_travel_modes() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return ok(request)

    routes = make(handler)
    legs = [
        await routes.travel(A, B, mode, FUTURE)
        for mode in ("walk", "bicycle", "transit", "rideshare")
    ]
    assert [b["travelMode"] for b in bodies] == ["WALK", "BICYCLE", "TRANSIT", "DRIVE"]
    # Traffic awareness is for driving only.
    assert ["routingPreference" in b for b in bodies] == [False, False, False, True]
    assert [leg.mode for leg in legs] == ["walk", "bicycle", "transit", "rideshare"]


async def test_past_departure_times_are_only_sent_for_transit() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return ok(request)

    routes = make(handler)
    await routes.travel(A, B, "drive", PAST)
    await routes.travel(A, B, "transit", PAST)
    assert "departureTime" not in bodies[0]
    assert bodies[1]["departureTime"] == "2026-11-19T19:00:00Z"


async def test_no_route_is_not_found_and_unknown_modes_are_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    routes = make(handler)
    with pytest.raises(ProviderUnavailable) as info:
        await routes.travel(A, B, "transit", FUTURE)
    assert (info.value.provider, info.value.reason) == ("google_routes", "not_found")
    with pytest.raises(ValueError):
        await routes.travel(A, B, "teleport", FUTURE)


@pytest.mark.parametrize(
    "body",
    [
        {"routes": [{}]},  # an empty route has no duration
        {"routes": [{"duration": "bad"}]},
        {"routes": [{"duration": None}]},
        {"routes": [{"duration": 165}]},
        {"routes": [{"duration": "165s", "distanceMeters": "far"}]},
        {"routes": [{"duration": "inf"}]},
        {"routes": [{"duration": "Infinity"}]},
        {"routes": [{"duration": "nan"}]},
        {"routes": [{"duration": "-5s"}]},
        {"routes": [{"duration": "-infs"}]},
        {"routes": [{"duration": "1e999s"}]},
        {"routes": [{"duration": "9" * 400 + "s"}]},
        {"routes": [{"duration": "165s", "distanceMeters": "inf"}]},
        {"routes": [{"duration": "165s", "distanceMeters": float("nan")}]},
        {"routes": [{"duration": "165s", "distanceMeters": -1}]},
        {"routes": [{"duration": float("nan")}]},
        {"routes": 5},
        {"routes": "165s"},  # a string where a list is expected
        {"routes": {"duration": "165s"}},
        {"routes": ["not a route"]},
        ["not", "an", "object"],
        "text",
    ],
)
async def test_malformed_ok_bodies_are_bad_responses(body: Any) -> None:
    with pytest.raises(ProviderUnavailable) as info:
        await make(lambda request: ok_json(body)).travel(A, B, "walk", FUTURE)
    assert (info.value.provider, info.value.reason) == ("google_routes", "bad_response")


@pytest.mark.parametrize(
    ("status", "reason"), [(429, "over_quota"), (403, "no_key"), (500, "down"), (400, "down")]
)
async def test_http_errors_become_provider_unavailable(status: int, reason: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"status": "RESOURCE_EXHAUSTED"}})

    with pytest.raises(ProviderUnavailable) as info:
        await make(handler).travel(A, B, "walk", FUTURE)
    assert (info.value.provider, info.value.reason) == ("google_routes", reason)
    assert KEY not in str(info.value)


async def test_timeout_becomes_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderUnavailable) as info:
        await make(handler).travel(A, B, "walk", FUTURE)
    assert info.value.reason == "down"


# --- FakeRoutes


@pytest.mark.parametrize(
    ("mode", "kmh"),
    [("walk", 4.5), ("bicycle", 14.0), ("transit", 20.0), ("drive", 30.0), ("rideshare", 30.0)],
)
async def test_fake_routes_are_distance_over_speed_plus_three_minutes(
    mode: str, kmh: float
) -> None:
    meters = A.distance_m(B)
    leg = await FakeRoutes().travel(A, B, mode, FUTURE)
    assert leg.mode == mode
    assert leg.distance_m == round(meters)
    assert leg.duration_minutes == round(meters / 1000 / kmh * 60 + 3)


async def test_fake_routes_are_deterministic_and_have_a_floor() -> None:
    fake = FakeRoutes()
    assert await fake.travel(A, B, "walk", FUTURE) == await fake.travel(A, B, "walk", PAST)
    here = await fake.travel(A, A, "drive", FUTURE)
    assert (here.duration_minutes, here.distance_m) == (3, 0)
    with pytest.raises(ValueError):
        await fake.travel(A, B, "teleport", FUTURE)
