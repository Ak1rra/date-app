"""The provider cache: key normalization (no database) and hit, miss and expiry (db)."""

import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.ai import ProviderCache
from app.providers.cache import RunMemo, bucket_time, cache_key, cached
from app.providers.events.ticketmaster import TicketmasterEvents
from app.providers.geocoding.google import GoogleGeocoder
from app.providers.http import Runtime
from app.providers.places.google import GooglePlaces
from app.providers.routes.google import GoogleRoutes
from app.providers.types import LatLng, Place, ProviderUnavailable, place_from_json, to_jsonable
from app.providers.weather.open_meteo import OpenMeteoWeather
from tests.conftest import DEAD_DB_URL

SessionMaker = async_sessionmaker[AsyncSession]
T0 = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)

db = pytest.mark.db


@pytest.fixture
async def sessionmaker(db_schema: str) -> AsyncIterator[SessionMaker]:
    engine = create_async_engine(db_schema)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def rows(sm: SessionMaker) -> list[ProviderCache]:
    async with sm() as session:
        return list((await session.execute(select(ProviderCache))).scalars())


class Counter:
    """A fetch that counts its calls."""

    def __init__(self, value: Any = None) -> None:
        self.calls = 0
        self.value = {"n": 1} if value is None else value

    async def __call__(self) -> Any:
        self.calls += 1
        return self.value


# --- key normalization (no database)


def test_key_is_a_sha256_hex_of_the_sorted_request() -> None:
    key = cache_key({"b": 1, "a": "x"})
    assert len(key) == 64 and int(key, 16) >= 0
    assert key == cache_key({"a": "x", "b": 1})
    assert key != cache_key({"a": "x", "b": 2})


def test_coordinates_are_rounded_to_three_decimals() -> None:
    base = {"near": LatLng(30.26721, -97.74311), "q": "pizza"}
    assert cache_key(base) == cache_key({"near": LatLng(30.26749, -97.74349), "q": "pizza"})
    assert cache_key(base) == cache_key({"near": {"lat": 30.2674, "lng": -97.7432}, "q": "pizza"})
    assert cache_key(base) != cache_key({"near": LatLng(30.2685, -97.74311), "q": "pizza"})
    assert cache_key(base) != cache_key({"near": LatLng(30.26721, -97.74311), "q": "sushi"})


def test_times_are_normalized_to_utc_and_buckets_group_them() -> None:
    plus_two = dt.timezone(dt.timedelta(hours=2))
    same_instant = (
        dt.datetime(2026, 11, 20, 19, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 11, 20, 21, 0, tzinfo=plus_two),
    )
    assert cache_key({"t": same_instant[0]}) == cache_key({"t": same_instant[1]})
    a = dt.datetime(2026, 11, 20, 19, 7, tzinfo=dt.UTC)
    b = dt.datetime(2026, 11, 20, 19, 14, 59, tzinfo=dt.UTC)
    c = dt.datetime(2026, 11, 20, 19, 15, tzinfo=dt.UTC)
    assert (
        bucket_time(a, 15) == bucket_time(b, 15) == dt.datetime(2026, 11, 20, 19, 0, tzinfo=dt.UTC)
    )
    assert bucket_time(c, 15) == c
    assert cache_key({"t": bucket_time(a, 15)}) == cache_key({"t": bucket_time(b, 15)})
    assert cache_key({"t": bucket_time(a, 15)}) != cache_key({"t": bucket_time(c, 15)})
    assert bucket_time(dt.datetime(2026, 11, 20, 18, 59), 60) == dt.datetime(2026, 11, 20, 18, 0)
    assert bucket_time(dt.datetime(2026, 11, 20, 18, 59), 60).tzinfo is None


def test_keys_reject_values_they_cannot_serialize() -> None:
    with pytest.raises(TypeError):
        cache_key({"x": object()})


async def test_an_unreachable_cache_database_is_skipped_not_fatal() -> None:
    engine = create_async_engine(DEAD_DB_URL)
    try:
        fetch = Counter()
        sm: SessionMaker = async_sessionmaker(engine)
        assert await cached(sm, "p", {"q": 1}, 60, fetch) == {"n": 1}
        assert await cached(sm, "p", {"q": 1}, 60, fetch) == {"n": 1}
        assert fetch.calls == 2
    finally:
        await engine.dispose()


# --- hit, miss, expiry (db)


@db
async def test_miss_fetches_and_stores_then_hit_returns_the_payload(
    sessionmaker: SessionMaker,
) -> None:
    fetch = Counter()
    request = {"op": "search", "near": LatLng(30.26721, -97.74311)}
    assert await cached(sessionmaker, "google_places", request, 3600, fetch, clock=lambda: T0) == {
        "n": 1
    }
    stored = await rows(sessionmaker)
    assert len(stored) == 1
    assert stored[0].provider == "google_places"
    assert stored[0].key == cache_key(request)
    assert stored[0].expires_at == T0 + dt.timedelta(hours=1)
    assert stored[0].payload == {"v": {"n": 1}}

    fetch.value = {"n": 2}  # a hit must not fetch, so the new value is never seen
    near_enough = {"op": "search", "near": LatLng(30.26749, -97.74349)}  # same key at 3 dp
    assert await cached(
        sessionmaker, "google_places", near_enough, 3600, fetch, clock=lambda: T0
    ) == {"n": 1}
    assert fetch.calls == 1
    assert len(await rows(sessionmaker)) == 1


@db
async def test_entries_expire_and_are_refreshed_in_place(sessionmaker: SessionMaker) -> None:
    fetch = Counter()
    request = {"q": "pizza"}
    await cached(sessionmaker, "p", request, 60, fetch, clock=lambda: T0)
    later = T0 + dt.timedelta(seconds=59)
    assert await cached(sessionmaker, "p", request, 60, fetch, clock=lambda: later) == {"n": 1}
    assert fetch.calls == 1
    expired = T0 + dt.timedelta(seconds=61)
    fetch.value = {"n": 2}
    assert await cached(sessionmaker, "p", request, 60, fetch, clock=lambda: expired) == {"n": 2}
    assert fetch.calls == 2
    (row,) = await rows(sessionmaker)  # upserted, not duplicated
    assert row.payload == {"v": {"n": 2}}
    assert row.expires_at == expired + dt.timedelta(seconds=60)


@db
async def test_providers_do_not_share_entries(sessionmaker: SessionMaker) -> None:
    one, two = Counter({"n": "one"}), Counter({"n": "two"})
    assert await cached(sessionmaker, "a", {"q": 1}, 60, one) == {"n": "one"}
    assert await cached(sessionmaker, "b", {"q": 1}, 60, two) == {"n": "two"}
    assert len(await rows(sessionmaker)) == 2


@db
async def test_a_zero_ttl_bypasses_the_cache(sessionmaker: SessionMaker) -> None:
    fetch = Counter()
    await cached(sessionmaker, "p", {"q": 1}, 0, fetch)
    await cached(sessionmaker, "p", {"q": 1}, 0, fetch)
    assert fetch.calls == 2
    assert await rows(sessionmaker) == []


@db
async def test_failures_are_not_cached(sessionmaker: SessionMaker) -> None:
    calls = 0

    async def fetch() -> Any:
        nonlocal calls
        calls += 1
        raise ProviderUnavailable("p", "down")

    for _ in range(2):
        with pytest.raises(ProviderUnavailable):
            await cached(sessionmaker, "p", {"q": 1}, 60, fetch)
    assert calls == 2
    assert await rows(sessionmaker) == []


@db
async def test_encode_and_decode_round_trip_dataclasses(sessionmaker: SessionMaker) -> None:
    place = Place("p1", "Cafe", None, LatLng(1.5, 2.5), 4.0, 3, 1, ["cafe"], None, None, None)
    fetch = Counter([place])
    kwargs = {
        "encode": to_jsonable,
        "decode": lambda payload: [place_from_json(p) for p in payload],
    }
    assert await cached(sessionmaker, "p", {"q": 1}, 60, fetch, **kwargs) == [place]
    assert await cached(sessionmaker, "p", {"q": 1}, 60, fetch, **kwargs) == [place]
    assert fetch.calls == 1


@db
async def test_an_entry_that_no_longer_decodes_is_refetched(sessionmaker: SessionMaker) -> None:
    async with sessionmaker() as session:
        session.add(
            ProviderCache(
                provider="p",
                key=cache_key({"q": 1}),
                payload={"v": "junk"},
                expires_at=T0 + dt.timedelta(days=1),
            )
        )
        await session.commit()
    fetch = Counter([])

    def decode(payload: Any) -> Any:
        return [place_from_json(p) for p in payload]  # a string is not a list of dicts

    assert (
        await cached(sessionmaker, "p", {"q": 1}, 60, fetch, decode=decode, clock=lambda: T0) == []
    )
    assert fetch.calls == 1


def google_handler(calls: list[httpx.Request]) -> Any:
    alba = {
        "id": "ChIJ-a",
        "displayName": {"text": "Alba"},
        "location": {"latitude": 47.6, "longitude": -122.3},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        host = request.url.host
        if host == "routes.googleapis.com":
            return httpx.Response(
                200, json={"routes": [{"duration": "300s", "distanceMeters": 900}]}
            )
        if host == "maps.googleapis.com":
            geometry = {"location": {"lat": 47.6, "lng": -122.3}}
            body = {"status": "OK", "results": [{"geometry": geometry, "place_id": "g1"}]}
            return httpx.Response(200, json=body)
        return httpx.Response(200, json=alba if request.method == "GET" else {"places": [alba]})

    return handler


@db
async def test_google_calls_write_no_provider_cache_row(sessionmaker: SessionMaker) -> None:
    calls: list[httpx.Request] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(google_handler(calls)))
    runtime = Runtime(session_factory=sessionmaker, memo=RunMemo())
    places = GooglePlaces("k", client=client, runtime=runtime)
    routes = GoogleRoutes("k", client=client, runtime=runtime)
    geocoder = GoogleGeocoder("k", client=client, runtime=runtime)
    near = LatLng(47.6001, -122.3301)
    await places.search("coffee", near, 1000)
    await places.details("ChIJ-a")
    await routes.travel(
        near, LatLng(47.62, -122.35), "walk", dt.datetime(2026, 11, 20, 20, tzinfo=dt.UTC)
    )
    await geocoder.geocode("Seattle")
    assert len(calls) == 4  # every one of them was a real call
    assert await rows(sessionmaker) == []  # and none of them was stored


@db
async def test_a_google_run_memo_reuses_results_without_the_database(
    sessionmaker: SessionMaker,
) -> None:
    calls: list[httpx.Request] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(google_handler(calls)))
    places = GooglePlaces(
        "k", client=client, runtime=Runtime(session_factory=sessionmaker, memo=RunMemo())
    )
    near = LatLng(47.6001, -122.3301)
    first = await places.search("coffee", near, 1000)
    assert await places.search("coffee", near, 1000) == first
    assert len(calls) == 1
    # A second run has a new memo, so it calls Google again.
    again = GooglePlaces(
        "k", client=client, runtime=Runtime(session_factory=sessionmaker, memo=RunMemo())
    )
    await again.search("coffee", near, 1000)
    assert len(calls) == 2
    assert await rows(sessionmaker) == []


@db
async def test_ticketmaster_still_caches_through_the_runtime(sessionmaker: SessionMaker) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"page": {"totalElements": 0}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    events = TicketmasterEvents(
        "k", ttl=60, client=client, runtime=Runtime(session_factory=sessionmaker)
    )
    near = LatLng(47.6001, -122.3301)
    start, end = (
        dt.datetime(2026, 11, 20, 18, tzinfo=dt.UTC),
        dt.datetime(2026, 11, 20, 23, tzinfo=dt.UTC),
    )
    await events.search(near, 5, start, end)
    await events.search(near, 5, start, end)
    assert len(calls) == 1
    (row,) = await rows(sessionmaker)
    assert row.provider == "ticketmaster"


@db
async def test_a_malformed_forecast_is_not_cached(sessionmaker: SessionMaker) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "bad"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    weather = OpenMeteoWeather(
        ttl=60,
        client=client,
        runtime=Runtime(session_factory=sessionmaker),
        now=dt.datetime(2026, 11, 20, 12, tzinfo=dt.UTC),
    )
    with pytest.raises(ProviderUnavailable) as info:
        await weather.forecast(
            LatLng(47.6, -122.3), dt.datetime(2026, 11, 21, 18), dt.datetime(2026, 11, 21, 20)
        )
    assert info.value.reason == "bad_response"
    assert await rows(sessionmaker) == []


@db
async def test_a_malformed_events_body_is_not_cached(sessionmaker: SessionMaker) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "bad"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    events = TicketmasterEvents(
        "k", ttl=60, client=client, runtime=Runtime(session_factory=sessionmaker)
    )
    start, end = (
        dt.datetime(2026, 11, 20, 18, tzinfo=dt.UTC),
        dt.datetime(2026, 11, 20, 23, tzinfo=dt.UTC),
    )
    with pytest.raises(ProviderUnavailable) as info:
        await events.search(LatLng(47.6, -122.3), 5, start, end)
    assert (info.value.provider, info.value.reason) == ("ticketmaster", "bad_response")
    assert await rows(sessionmaker) == []


@db
async def test_a_200_that_is_not_json_is_a_bad_response_and_is_not_cached(
    sessionmaker: SessionMaker,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    events = TicketmasterEvents(
        "k", ttl=60, client=client, runtime=Runtime(session_factory=sessionmaker)
    )
    start, end = (
        dt.datetime(2026, 11, 20, 18, tzinfo=dt.UTC),
        dt.datetime(2026, 11, 20, 23, tzinfo=dt.UTC),
    )
    with pytest.raises(ProviderUnavailable) as info:
        await events.search(LatLng(47.6, -122.3), 5, start, end)
    assert (info.value.provider, info.value.reason) == ("ticketmaster", "bad_response")
    assert await rows(sessionmaker) == []
