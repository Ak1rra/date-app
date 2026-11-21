"""GooglePlaces (httpx.MockTransport, no network) and FakePlaces."""

import json
from datetime import datetime
from typing import Any

import httpx
import pytest

from app.providers.cache import RunMemo
from app.providers.http import Runtime
from app.providers.places.fake import DEMO_CENTER, SPREAD_M, VENUES, FakePlaces
from app.providers.places.google import (
    DETAILS_FIELD_MASK,
    DETAILS_URL,
    SEARCH_FIELD_MASK,
    SEARCH_URL,
    GooglePlaces,
)
from app.providers.types import LatLng, ProviderUnavailable
from tests.provider_helpers import ok_json

KEY = "secret-key"
NEAR = LatLng(47.6, -122.33)
FRIDAY_EVENING = datetime(2026, 11, 20, 20, 0)  # a Friday


def hours(day: int, open_h: int, close_h: int) -> dict[str, Any]:
    return {
        "periods": [
            {
                "open": {
                    "date": {"year": 2026, "month": 11, "day": 20},
                    "day": day,
                    "hour": open_h,
                },
                "close": {"day": day, "hour": close_h, "minute": 0},
            }
        ]
    }


GOOGLE_PLACE = {
    "id": "ChIJ-a",
    "displayName": {"text": "Cafe Alba", "languageCode": "en"},
    "formattedAddress": "1 Pike St, Seattle",
    "location": {"latitude": 47.61, "longitude": -122.34},
    "rating": 4.6,
    "userRatingCount": 321,
    "priceLevel": "PRICE_LEVEL_MODERATE",
    "regularOpeningHours": hours(5, 9, 22),
    "websiteUri": "https://alba.example",
    "googleMapsUri": "https://maps.google.com/?cid=1",
    "accessibilityOptions": {
        "wheelchairAccessibleEntrance": True,
        "wheelchairAccessibleSeating": False,
    },
    "types": ["cafe", "food"],
}
CLOSED_FRIDAY = {**GOOGLE_PLACE, "id": "ChIJ-b", "regularOpeningHours": hours(1, 9, 17)}
NO_HOURS = {"id": "ChIJ-c", "displayName": {"text": "Mystery"}}


def make(handler: Any) -> GooglePlaces:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GooglePlaces(KEY, client=client)


async def test_search_sends_the_documented_request_and_parses_places() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"places": [GOOGLE_PLACE]})

    places = await make(handler).search(
        "date night dinner", NEAR, 2000, included_type="restaurant", price_levels=[0, 2, 3]
    )
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == SEARCH_URL
    assert request.headers["x-goog-api-key"] == KEY
    assert request.headers["x-goog-fieldmask"] == SEARCH_FIELD_MASK
    assert " " not in SEARCH_FIELD_MASK
    body = json.loads(request.content)
    assert body["textQuery"] == "date night dinner"
    assert body["includedType"] == "restaurant"
    assert body["strictTypeFiltering"] is True
    assert body["pageSize"] == 20  # always a full page; max_results is applied afterwards
    # Free is not an accepted filter value, so a request that includes it is not price filtered
    # server side (the paid levels alone would drop the free places); the results are.
    assert "priceLevels" not in body
    assert "locationBias" not in body
    rectangle = body["locationRestriction"]["rectangle"]
    assert rectangle["low"]["latitude"] < 47.6 < rectangle["high"]["latitude"]
    assert rectangle["low"]["longitude"] < -122.33 < rectangle["high"]["longitude"]
    corner = LatLng(rectangle["high"]["latitude"], rectangle["high"]["longitude"])
    assert 2000 < NEAR.distance_m(corner) < 2000 * 1.5  # the box around a 2 km circle
    (place,) = places
    assert place.place_id == "ChIJ-a"
    assert place.name == "Cafe Alba"
    assert place.location == LatLng(47.61, -122.34)
    assert (place.rating, place.rating_count, place.price_level) == (4.6, 321, 2)
    assert place.types == ["cafe", "food"]
    assert place.website == "https://alba.example"
    assert place.accessibility == {
        "wheelchairAccessibleEntrance": True,
        "wheelchairAccessibleSeating": False,
    }
    assert place.opening_periods == [
        {"open": {"day": 5, "hour": 9, "minute": 0}, "close": {"day": 5, "hour": 22, "minute": 0}}
    ]
    assert place.attributions == ["Places data © Google"]


async def test_search_sends_paid_price_levels_when_free_is_not_wanted() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"places": [GOOGLE_PLACE]})

    places = await make(handler).search("x", NEAR, 5000, price_levels=[3, 2])
    assert bodies[0]["priceLevels"] == ["PRICE_LEVEL_MODERATE", "PRICE_LEVEL_EXPENSIVE"]
    assert [p.place_id for p in places] == ["ChIJ-a"]  # moderate is wanted


async def test_search_caps_the_radius_and_omits_unset_filters() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={})

    assert await make(handler).search("park", NEAR, 90_000, max_results=50) == []
    (body,) = bodies
    rectangle = body["locationRestriction"]["rectangle"]
    corner = LatLng(rectangle["high"]["latitude"], rectangle["high"]["longitude"])
    assert 50_000 < NEAR.distance_m(corner) < 50_000 * 1.5  # capped at 50 km, not 90 km
    assert body["pageSize"] == 20
    assert not {"includedType", "priceLevels", "strictTypeFiltering"} & body.keys()


async def test_search_open_at_filters_client_side_and_keeps_unknown_hours() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"places": [GOOGLE_PLACE, CLOSED_FRIDAY, NO_HOURS]})

    places = await make(handler).search("x", NEAR, 5000, open_at=FRIDAY_EVENING)
    assert [p.place_id for p in places] == ["ChIJ-a", "ChIJ-c"]
    assert places[1].opening_periods is None
    assert places[1].price_level is None


async def test_search_respects_max_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"places": [GOOGLE_PLACE, CLOSED_FRIDAY, NO_HOURS]})

    assert len(await make(handler).search("x", NEAR, 5000, max_results=2)) == 2


FAR_AWAY = {**GOOGLE_PLACE, "id": "ChIJ-far", "location": {"latitude": 47.7, "longitude": -122.33}}


async def test_search_drops_places_beyond_the_radius() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"places": [FAR_AWAY, GOOGLE_PLACE, NO_HOURS]})

    # FAR_AWAY is about 11 km north; GOOGLE_PLACE about 1.3 km away; NO_HOURS has no location
    # (it came back inside the restriction, so it is kept).
    near_only = await make(handler).search("x", NEAR, 2000)
    assert [p.place_id for p in near_only] == ["ChIJ-a", "ChIJ-c"]
    everything = await make(handler).search("x", NEAR, 20_000)
    assert [p.place_id for p in everything] == ["ChIJ-far", "ChIJ-a", "ChIJ-c"]


@pytest.mark.parametrize(
    "location",
    [
        {"latitude": "Infinity", "longitude": -122.33},
        {"latitude": 47.6, "longitude": "-Infinity"},
        {"latitude": "NaN", "longitude": -122.33},
        {"latitude": 91, "longitude": -122.33},
        {"latitude": -90.5, "longitude": -122.33},
        {"latitude": 47.6, "longitude": 181},
        {"latitude": 47.6, "longitude": -180.1},
    ],
)
async def test_a_place_with_impossible_coordinates_is_dropped_not_fatal(
    location: dict[str, Any],
) -> None:
    bad = {**GOOGLE_PLACE, "id": "ChIJ-bad", "location": location}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"places": [GOOGLE_PLACE, bad, NO_HOURS]})

    places = await make(handler).search("x", NEAR, 5000)
    assert [p.place_id for p in places] == ["ChIJ-a", "ChIJ-c"]
    # A single place with impossible coordinates is not a place we can use.
    with pytest.raises(ProviderUnavailable) as info:
        await make(lambda request: httpx.Response(200, json=bad)).details("ChIJ-bad")
    assert info.value.reason == "bad_response"


FREE = {**GOOGLE_PLACE, "id": "ChIJ-free", "priceLevel": "PRICE_LEVEL_FREE"}
CHEAP = {**GOOGLE_PLACE, "id": "ChIJ-cheap", "priceLevel": "PRICE_LEVEL_INEXPENSIVE"}


async def test_search_filters_by_price_after_the_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"places": [FREE, CHEAP, GOOGLE_PLACE, NO_HOURS]})

    places = make(handler)
    free = await places.search("x", NEAR, 5000, price_levels=[0])
    assert [p.place_id for p in free] == ["ChIJ-free"]
    mixed = await places.search("x", NEAR, 5000, price_levels=[0, 1])
    assert [p.place_id for p in mixed] == ["ChIJ-free", "ChIJ-cheap"]
    # Unknown prices are excluded once a filter is given, and kept when it is not.
    assert "ChIJ-c" not in [p.place_id for p in mixed]
    everything = await places.search("x", NEAR, 5000)
    assert [p.place_id for p in everything] == ["ChIJ-free", "ChIJ-cheap", "ChIJ-a", "ChIJ-c"]


BAD_PLACES: list[Any] = [
    {"displayName": {"text": "no id"}},
    {"id": ""},
    {"id": "x", "regularOpeningHours": {"periods": [{"close": {}}]}},
    {"id": "x", "regularOpeningHours": {"periods": [{"open": {"day": "mon"}}]}},
    {"id": "x", "regularOpeningHours": {"periods": [{"open": {"day": 9}}]}},
    {"id": "x", "rating": float("nan")},
    {"id": "x", "rating": "five"},
    {"id": "x", "types": "cafe"},  # a string is not a list of types
    {"id": "x", "accessibilityOptions": ["wheelchair"]},
    {"id": "x", "displayName": {}},
    "not a place",
    5,
]
# A place whose only problem is its coordinates is skipped by search, but fails details.
BAD_LOCATIONS: list[Any] = [
    {"id": "x", "location": {}},  # present but empty is not "absent"
    {"id": "x", "location": {"latitude": 1}},
    {"id": "x", "location": {"latitude": "north", "longitude": 1}},
    {"id": "x", "location": {"latitude": float("nan"), "longitude": 1}},
    {"id": "x", "location": {"latitude": "Infinity", "longitude": 1}},
    {"id": "x", "location": {"latitude": 91, "longitude": 1}},
    {"id": "x", "location": {"latitude": 1, "longitude": -181}},
    {"id": "x", "location": "here"},
]
BAD_ENVELOPES: list[Any] = [
    {"places": 5},
    {"places": "cafe"},
    {"places": {"id": "x"}},
    ["not", "an", "object"],
    "text",
]


@pytest.mark.parametrize("bad", BAD_PLACES)
async def test_a_malformed_place_fails_the_search_and_details(bad: Any) -> None:
    good = [{"places": [GOOGLE_PLACE, bad]}, bad]
    bodies = iter(good)
    places = make(lambda request: ok_json(next(bodies)))
    with pytest.raises(ProviderUnavailable) as info:
        await places.search("x", NEAR, 5000)
    assert (info.value.provider, info.value.reason) == ("google_places", "bad_response")
    with pytest.raises(ProviderUnavailable) as info:
        await places.details("p")
    assert info.value.reason == "bad_response"


@pytest.mark.parametrize("bad", BAD_LOCATIONS)
async def test_a_place_with_a_bad_location_is_skipped_by_search_and_fails_details(
    bad: Any,
) -> None:
    bodies = iter([{"places": [GOOGLE_PLACE, bad, NO_HOURS]}, bad])
    places = make(lambda request: ok_json(next(bodies)))
    found = await places.search("x", NEAR, 5000)
    assert [p.place_id for p in found] == ["ChIJ-a", "ChIJ-c"]  # the others are returned
    with pytest.raises(ProviderUnavailable) as info:
        await places.details("p")
    assert (info.value.provider, info.value.reason) == ("google_places", "bad_response")


@pytest.mark.parametrize("body", BAD_ENVELOPES)
async def test_a_malformed_search_envelope_is_a_bad_response(body: Any) -> None:
    with pytest.raises(ProviderUnavailable) as info:
        await make(lambda request: ok_json(body)).search("x", NEAR, 1000)
    assert (info.value.provider, info.value.reason) == ("google_places", "bad_response")


@pytest.mark.parametrize("body", [{}, {"error": "x"}, *BAD_ENVELOPES])
async def test_malformed_details_bodies_are_bad_responses(body: Any) -> None:
    with pytest.raises(ProviderUnavailable) as info:
        await make(lambda request: ok_json(body)).details("p")
    assert (info.value.provider, info.value.reason) == ("google_places", "bad_response")


async def test_a_search_with_no_places_key_is_an_empty_result() -> None:
    assert await make(lambda request: ok_json({})).search("x", NEAR, 1000) == []


async def test_types_that_are_a_list_of_strings_are_kept_and_a_missing_one_is_empty() -> None:
    body = {"places": [GOOGLE_PLACE, NO_HOURS]}
    found = await make(lambda request: ok_json(body)).search("x", NEAR, 5000)
    assert found[0].types == ["cafe", "food"] and found[1].types == []


async def test_the_run_memo_reuses_identical_requests_within_one_run() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json=GOOGLE_PLACE if request.method == "GET" else {"places": [GOOGLE_PLACE]}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    memo = RunMemo()
    places = GooglePlaces(KEY, client=client, runtime=Runtime(memo=memo))
    first = await places.search("coffee", NEAR, 2000)
    assert await places.search("coffee", NEAR, 2000) == first
    # The filters run on every call, so they are not part of what is memoized.
    assert await places.search("coffee", NEAR, 2000, open_at=FRIDAY_EVENING) == first
    assert len(calls) == 1
    await places.details("ChIJ-a")
    await places.details("ChIJ-a")
    assert len(calls) == 2
    # Another run (another memo) fetches again; so does a provider with no memo.
    other = GooglePlaces(KEY, client=client, runtime=Runtime(memo=RunMemo()))
    await other.search("coffee", NEAR, 2000)
    await GooglePlaces(KEY, client=client).search("coffee", NEAR, 2000)
    assert len(calls) == 4


async def test_details_requests_the_place_with_the_details_mask() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=GOOGLE_PLACE)

    place = await make(handler).details("ChIJ-a")
    (request,) = seen
    assert request.method == "GET"
    assert str(request.url) == DETAILS_URL + "ChIJ-a"
    assert request.headers["x-goog-fieldmask"] == DETAILS_FIELD_MASK
    assert not DETAILS_FIELD_MASK.startswith("places.")
    assert place.name == "Cafe Alba"


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (429, "over_quota"),
        (403, "no_key"),
        (401, "no_key"),
        (404, "not_found"),
        (500, "down"),
        (400, "down"),
    ],
)
async def test_http_errors_become_provider_unavailable(status: int, reason: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": f"bad key {KEY}"}})

    places = make(handler)
    for call in (places.search("x", NEAR, 1000), places.details("p")):
        with pytest.raises(ProviderUnavailable) as info:
            await call
        assert (info.value.provider, info.value.reason) == ("google_places", reason)
        assert KEY not in str(info.value)


async def test_timeouts_become_down() -> None:
    def times_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderUnavailable) as info:
        await make(times_out).details("p")
    assert info.value.reason == "down"


async def test_a_200_that_is_not_json_is_a_bad_response() -> None:
    def not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with pytest.raises(ProviderUnavailable) as info:
        await make(not_json).details("p")
    assert (info.value.provider, info.value.reason) == ("google_places", "bad_response")


@pytest.mark.parametrize("max_results", [0, -1])
async def test_search_with_no_results_wanted_makes_no_call(max_results: int) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"places": [GOOGLE_PLACE]})

    assert await make(handler).search("x", NEAR, 5000, max_results=max_results) == []
    assert calls == []


# --- FakePlaces

fake = FakePlaces()


async def search_all(**kwargs: Any) -> list[Any]:
    return await fake.search(
        kwargs.pop("query", ""), DEMO_CENTER, 20_000, max_results=100, **kwargs
    )


async def test_fake_has_thirty_stable_demo_venues() -> None:
    places = await search_all()
    assert len(places) == len(VENUES) == 30
    assert all(p.place_id.startswith("demo-place-") for p in places)
    assert len({p.place_id for p in places}) == 30
    assert await search_all() == places  # deterministic
    assert await FakePlaces().search("", DEMO_CENTER, 20_000, max_results=100) == places


async def test_fake_places_are_realistic() -> None:
    for place in await search_all():
        assert place.location is not None
        assert DEMO_CENTER.distance_m(place.location) <= SPREAD_M * 1.5
        assert place.opening_periods
        assert place.price_level is not None and 0 <= place.price_level <= 4
        assert place.rating is not None and 3.5 <= place.rating <= 5
        assert place.attributions
        for period in place.opening_periods:
            assert 0 <= period["open"]["day"] <= 6


async def test_fake_details_match_search_and_unknown_ids_are_unavailable() -> None:
    first = (await search_all())[0]
    assert await fake.details(first.place_id) == first
    with pytest.raises(ProviderUnavailable) as info:
        await fake.details("demo-place-nope")
    assert info.value.reason == "not_found"


async def test_fake_filters_by_query_type_price_radius_and_count() -> None:
    assert [p.place_id for p in await search_all(query="pottery class")] == [
        "demo-place-clay-and-kiln"
    ]
    parks = await search_all(included_type="park")
    assert parks and all("park" in p.types for p in parks)
    free = await search_all(price_levels=[0])
    assert free and all(p.price_level == 0 for p in free)
    assert {p.price_level for p in await search_all(price_levels=[1, 4])} == {1, 4}
    assert await fake.search("", DEMO_CENTER, 1) == []
    assert len(await fake.search("", DEMO_CENTER, 20_000, max_results=3)) == 3


@pytest.mark.parametrize("max_results", [0, -1])
async def test_fake_search_with_no_results_wanted_returns_nothing(max_results: int) -> None:
    assert await fake.search("", DEMO_CENTER, 20_000, max_results=max_results) == []
    assert await search_all(query="zzzz-nothing") == []
    # Nearest first.
    distances = [DEMO_CENTER.distance_m(p.location) for p in await search_all() if p.location]
    assert distances == sorted(distances)


async def test_fake_open_at_uses_the_opening_periods() -> None:
    monday_noon = datetime(2026, 11, 23, 12, 0)
    open_monday = {p.place_id for p in await search_all(open_at=monday_noon)}
    assert "demo-place-city-art-museum" not in open_monday  # museums close on Mondays
    assert "demo-place-riverside-park" in open_monday  # always open
    tuesday_noon = datetime(2026, 11, 24, 12, 0)
    assert "demo-place-city-art-museum" in {
        p.place_id for p in await search_all(open_at=tuesday_noon)
    }
    # Bars run past midnight: Saturday 01:00 belongs to Friday's opening.
    saturday_1am = datetime(2026, 11, 21, 1, 0)
    assert "demo-place-copper-still" in {p.place_id for p in await search_all(open_at=saturday_1am)}
    assert "demo-place-copper-still" not in {
        p.place_id for p in await search_all(open_at=datetime(2026, 11, 21, 10, 0))
    }
