"""TicketmasterEvents (httpx.MockTransport, no network) and FakeEvents."""

from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any

import httpx
import pytest

from app.providers.events.fake import MAX_DAYS, PER_DAY, FakeEvents
from app.providers.events.ticketmaster import EVENTS_URL, TicketmasterEvents, geohash
from app.providers.types import LatLng, ProviderUnavailable
from tests.provider_helpers import ok_json

KEY = "tm-secret"
NEAR = LatLng(41.8781, -87.6298)
START = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)
END = datetime(2026, 11, 20, 23, 59, 59, tzinfo=UTC)

FULL_EVENT = {
    "id": "G5v0Z9",
    "name": "Jazz at the Green Mill",
    "url": "https://www.ticketmaster.com/event/G5v0Z9",
    "dates": {
        "start": {
            "localDate": "2026-11-20",
            "localTime": "20:00:00",
            "dateTime": "2026-11-21T02:00:00Z",
        },
        "timezone": "America/Chicago",
    },
    "priceRanges": [{"type": "standard", "currency": "USD", "min": 25.0, "max": 60.0}],
    "classifications": [{"segment": {"name": "Music"}, "genre": {"name": "Jazz"}}],
    "_embedded": {
        "venues": [
            {
                "name": "Green Mill",
                "address": {"line1": "4802 N Broadway"},
                "city": {"name": "Chicago"},
                "state": {"stateCode": "IL"},
                "location": {"latitude": "41.9654", "longitude": "-87.6600"},
            }
        ]
    },
}
TBA_EVENT = {
    "id": "TBA1",
    "name": "Mystery Night",
    "dates": {"start": {"localDate": "2026-11-21", "timeTBA": True}},
}
NO_DATE_EVENT = {"id": "BAD", "name": "No date", "dates": {"start": {}}}


def make(handler: Any, **kwargs: Any) -> TicketmasterEvents:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TicketmasterEvents(KEY, client=client, **kwargs)


def test_geohash_matches_the_reference_value() -> None:
    assert geohash(LatLng(57.64911, 10.40744), 9) == "u4pruydqq"
    assert len(geohash(NEAR)) == 9


async def test_search_sends_the_documented_request_and_parses_events() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "page": {"totalElements": 3},
                "_embedded": {"events": [FULL_EVENT, TBA_EVENT, NO_DATE_EVENT]},
            },
        )

    events = await make(handler).search(
        NEAR, 15, START, END, keywords="jazz", segment="Music", max_results=5
    )
    (request,) = seen
    assert request.method == "GET"
    assert str(request.url).startswith(EVENTS_URL)
    params = dict(request.url.params)
    assert params == {
        "apikey": KEY,
        "geoPoint": geohash(NEAR),
        "radius": "15",
        "unit": "km",
        "startDateTime": "2026-11-20T18:00:00Z",
        "endDateTime": "2026-11-20T23:59:59Z",
        "sort": "date,asc",
        "size": "5",
        "keyword": "jazz",
        "segmentName": "Music",
    }
    assert [e.event_id for e in events] == ["G5v0Z9", "TBA1"]  # the one with no date is skipped
    jazz, tba = events
    assert jazz.name == "Jazz at the Green Mill"
    assert jazz.local_date == date(2026, 11, 20)
    assert jazz.local_time == time(20, 0)
    assert jazz.starts_at == datetime(2026, 11, 21, 2, 0, tzinfo=UTC)
    assert jazz.venue_name == "Green Mill"
    assert jazz.venue_address == "4802 N Broadway, Chicago, IL"
    assert jazz.location == LatLng(41.9654, -87.66)
    assert (jazz.price_min, jazz.price_max, jazz.currency) == (25.0, 60.0, "USD")
    assert (jazz.segment, jazz.genre) == ("Music", "Jazz")
    assert jazz.url == "https://www.ticketmaster.com/event/G5v0Z9"
    assert tba.local_time is None and tba.starts_at is None and tba.location is None
    assert tba.price_min is None and tba.venue_name is None


async def test_search_converts_other_time_zones_to_utc() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"page": {"totalElements": 0}})

    chicago = timedelta(hours=-6)
    start = datetime(2026, 11, 20, 18, 0, tzinfo=timezone(chicago))
    assert await make(handler).search(NEAR, 5, start, start + timedelta(hours=3)) == []
    params = dict(seen[0].url.params)
    assert params["startDateTime"] == "2026-11-21T00:00:00Z"
    assert params["endDateTime"] == "2026-11-21T03:00:00Z"
    assert "keyword" not in params and "segmentName" not in params


@pytest.mark.parametrize(
    ("status", "reason"), [(429, "over_quota"), (401, "no_key"), (500, "down"), (400, "down")]
)
async def test_http_errors_become_provider_unavailable(status: int, reason: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"fault": {"faultstring": "Rate limit quota violation"}})

    with pytest.raises(ProviderUnavailable) as info:
        await make(handler).search(NEAR, 5, START, END)
    assert (info.value.provider, info.value.reason) == ("ticketmaster", reason)
    assert KEY not in str(info.value)  # the key is in the URL; it must never leak


ONE = {"totalElements": 1}
GOOD_EVENT = {"id": "e1", "dates": {"start": {"localDate": "2026-11-20"}}}


def page_with(event: Any) -> dict[str, Any]:
    return {"page": ONE, "_embedded": {"events": [event]}}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"error": "x"},
        {"fault": {"faultstring": "Invalid ApiKey"}},
        ["not", "an", "object"],
        "text",
        {"_embedded": {"events": [GOOD_EVENT]}},  # `page` is required
        {"page": {}},  # an empty object where one with required fields is needed
        {"page": {"totalElements": "many"}},
        {"page": {"totalElements": -1}},
        {"page": {"totalElements": float("nan")}},
        {"page": {"totalElements": 2}},  # events may only be missing from an empty result
        {"page": ONE, "_embedded": {}},
        {"page": ONE, "_embedded": {"events": "jazz"}},  # a string where a list is expected
        {"page": ONE, "_embedded": {"events": {"id": "e1"}}},
        {"page": ONE, "_embedded": 5},
        page_with({"name": "no id"}),
        page_with("not an event"),
        page_with({**GOOD_EVENT, "dates": {"start": {"localDate": "2026-13-45"}}}),
        page_with({**GOOD_EVENT, "dates": "tonight"}),
        page_with({**GOOD_EVENT, "priceRanges": [{"min": float("nan")}]}),
        page_with({**GOOD_EVENT, "priceRanges": "free"}),
        page_with({**GOOD_EVENT, "classifications": "Music"}),
        page_with({**GOOD_EVENT, "_embedded": {"venues": [{"location": {}}]}}),
        page_with({**GOOD_EVENT, "_embedded": {"venues": [{"location": {"latitude": "95"}}]}}),
        page_with(
            {
                **GOOD_EVENT,
                "_embedded": {"venues": [{"location": {"latitude": "1", "longitude": "181"}}]},
            }
        ),
        page_with({**GOOD_EVENT, "_embedded": {"venues": "Green Mill"}}),
    ],
)
async def test_malformed_ok_bodies_are_bad_responses(body: Any) -> None:
    with pytest.raises(ProviderUnavailable) as info:
        await make(lambda request: ok_json(body)).search(NEAR, 5, START, END)
    assert (info.value.provider, info.value.reason) == ("ticketmaster", "bad_response")


async def test_an_error_body_is_not_an_empty_event_list() -> None:
    body = {"error": "something went wrong"}
    with pytest.raises(ProviderUnavailable) as info:
        await make(lambda request: ok_json(body)).search(NEAR, 5, START, END)
    assert info.value.reason == "bad_response"


async def test_an_empty_result_is_valid_with_or_without_embedded() -> None:
    assert (
        await make(lambda r: ok_json({"page": {"totalElements": 0}})).search(NEAR, 5, START, END)
        == []
    )
    empty = {"page": {"totalElements": 0}, "_embedded": {"events": []}}
    assert await make(lambda r: ok_json(empty)).search(NEAR, 5, START, END) == []


@pytest.mark.parametrize("max_results", [0, -3])
async def test_no_results_wanted_means_no_call(max_results: int) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return ok_json(page_with(GOOD_EVENT))

    provider = make(handler)
    assert await provider.search(NEAR, 5, START, END, max_results=max_results) == []
    assert calls == []


async def test_a_response_with_more_events_than_max_results_is_trimmed() -> None:
    events = [{**GOOD_EVENT, "id": f"e{n}"} for n in range(5)]
    body = {"page": {"totalElements": 5}, "_embedded": {"events": events}}
    found = await make(lambda r: ok_json(body)).search(NEAR, 5, START, END, max_results=2)
    assert [e.event_id for e in found] == ["e0", "e1"]


async def test_timeout_becomes_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    with pytest.raises(ProviderUnavailable) as info:
        await make(handler).search(NEAR, 5, START, END)
    assert info.value.reason == "down"
    assert KEY not in str(info.value) and info.value.__cause__ is None


# --- FakeEvents

fake = FakeEvents()


async def test_fake_events_are_deterministic_and_dated() -> None:
    start, end = datetime(2026, 11, 20, 9), datetime(2026, 11, 22, 23)
    events = await fake.search(NEAR, 10, start, end, max_results=100)
    assert events == await FakeEvents().search(NEAR, 10, start, end, max_results=100)
    assert len(events) == 3 * PER_DAY
    assert {e.local_date for e in events} == {date(2026, 11, 20 + n) for n in range(3)}
    assert all(e.event_id.startswith("demo-event-") and e.url for e in events)
    assert all(e.location is not None and NEAR.distance_m(e.location) < 3000 for e in events)
    assert events == sorted(
        events, key=lambda e: (e.local_date, e.local_time or time(0), e.event_id)
    )


async def test_fake_events_filter_and_cap() -> None:
    start, end = datetime(2026, 11, 1), datetime(2026, 11, 10)
    everything = await fake.search(NEAR, 10, start, end, max_results=500)
    music = await fake.search(NEAR, 10, start, end, segment="music", max_results=500)
    assert music and all(e.segment == "Music" for e in music)
    assert len(music) < len(everything)
    jazz = await fake.search(NEAR, 10, start, end, keywords="jazz", max_results=500)
    assert jazz and all("jazz" in f"{e.name} {e.genre}".lower() for e in jazz)
    assert len(await fake.search(NEAR, 10, start, end, max_results=4)) == 4


@pytest.mark.parametrize("max_results", [0, -3])
async def test_fake_events_with_no_results_wanted_return_nothing(max_results: int) -> None:
    start, end = datetime(2026, 11, 1), datetime(2026, 11, 10)
    assert await fake.search(NEAR, 10, start, end, max_results=max_results) == []


async def test_fake_events_only_start_inside_the_requested_window() -> None:
    day = datetime(2026, 11, 20)
    whole_day = await fake.search(NEAR, 10, day, day + timedelta(days=1), max_results=100)
    assert len(whole_day) == PER_DAY
    cut = datetime(2026, 11, 20, 15, 0)
    afternoon = await fake.search(NEAR, 10, cut, day + timedelta(days=1), max_results=100)
    morning = await fake.search(NEAR, 10, day, cut, max_results=100)
    assert all(
        e.starts_at is not None and e.starts_at >= cut.replace(tzinfo=UTC) for e in afternoon
    )
    assert all(e.starts_at is not None and e.starts_at < cut.replace(tzinfo=UTC) for e in morning)
    # [start, end): an event starting exactly at `end` is out, one starting at `start` is in.
    assert {e.event_id for e in afternoon} | {e.event_id for e in morning} == {
        e.event_id for e in whole_day
    }
    assert not {e.event_id for e in afternoon} & {e.event_id for e in morning}
    first = whole_day[0]
    assert first.starts_at is not None
    assert first not in await fake.search(NEAR, 10, day, first.starts_at, max_results=100)
    assert first in await fake.search(
        NEAR, 10, first.starts_at, first.starts_at + timedelta(minutes=1), max_results=100
    )
    # An empty or inverted window finds nothing, and zone-aware bounds are read as UTC.
    assert await fake.search(NEAR, 10, cut, cut) == []
    assert await fake.search(NEAR, 10, cut, day) == []
    aware = await fake.search(
        NEAR, 10, cut.replace(tzinfo=UTC), day.replace(tzinfo=UTC) + timedelta(days=1)
    )
    assert aware == afternoon


async def test_fake_events_only_come_from_within_the_radius() -> None:
    start, end = datetime(2026, 11, 1), datetime(2026, 11, 15)
    wide = await fake.search(NEAR, 10, start, end, max_results=500)
    assert wide
    for radius_km in (1, 2):
        narrow = await fake.search(NEAR, radius_km, start, end, max_results=500)
        assert 0 < len(narrow) < len(wide)
        assert all(
            e.location is not None and NEAR.distance_m(e.location) <= radius_km * 1000
            for e in narrow
        )
    assert await fake.search(NEAR, 0, start, end) == []


async def test_fake_events_cover_at_most_two_weeks() -> None:
    events = await fake.search(
        NEAR, 10, datetime(2026, 1, 1), datetime(2026, 12, 31), max_results=1000
    )
    assert len({e.local_date for e in events}) == MAX_DAYS
