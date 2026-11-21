"""GoogleGeocoder (httpx.MockTransport, no network)."""

from typing import Any

import httpx
import pytest

from app.providers.cache import RunMemo
from app.providers.geocoding.base import GeocodeNotFound
from app.providers.geocoding.fake import FakeGeocoder
from app.providers.geocoding.google import GEOCODE_URL, GoogleGeocoder, label_for
from app.providers.geocoding.schema import Component
from app.providers.http import Runtime
from app.providers.types import ProviderUnavailable
from tests.provider_helpers import ok_json

KEY = "geo-secret"


def component(name: str, *types: str, short: str | None = None) -> dict[str, Any]:
    return {"long_name": name, "short_name": short or name, "types": list(types)}


HYDE_PARK = {
    "status": "OK",
    "results": [
        {
            "formatted_address": "4300 Duval Rd, Austin, TX 78727, USA",
            "address_components": [
                component("4300", "street_number"),
                component("Hyde Park", "neighborhood", "political"),
                component("Austin", "locality", "political"),
                component("Texas", "administrative_area_level_1", "political", short="TX"),
            ],
            "geometry": {"location": {"lat": 30.3052, "lng": -97.7253}},
            "place_id": "ChIJ-hyde",
        }
    ],
}


def labels(result: dict[str, Any]) -> str:
    return label_for([Component.model_validate(c) for c in result.get("address_components", [])])


def make(handler: Any) -> GoogleGeocoder:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GoogleGeocoder(KEY, client=client)


async def test_geocode_sends_the_query_and_returns_a_neighbourhood_label() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=HYDE_PARK)

    result = await make(handler).geocode("  hyde   park, austin ")
    (request,) = seen
    assert str(request.url).startswith(GEOCODE_URL)
    assert dict(request.url.params) == {"address": "hyde park, austin", "key": KEY}
    assert result.label == "Hyde Park, Austin"
    assert (result.lat, result.lng) == (30.3052, -97.7253)
    assert result.place_id == "ChIJ-hyde"


def test_label_prefers_sublocalities_then_city_and_state_then_state_and_country() -> None:
    sub = {
        "address_components": [
            component("Queens", "sublocality_level_1"),
            component("New York", "locality"),
        ]
    }
    assert labels(sub) == "Queens, New York"
    city = {
        "address_components": [
            component("Austin", "locality"),
            component("Texas", "administrative_area_level_1", short="TX"),
        ]
    }
    assert labels(city) == "Austin, TX"
    region = {
        "address_components": [
            component("Texas", "administrative_area_level_1", short="TX"),
            component("United States", "country", short="US"),
        ]
    }
    assert labels(region) == "Texas, United States"


def test_label_never_falls_back_to_the_street() -> None:
    street_only = {
        "formatted_address": "4300 Duval Rd, Austin, TX 78727, USA",
        "address_components": [
            component("4300", "street_number"),
            component("Duval Road", "route"),
            component("78727", "postal_code"),
        ],
    }
    assert labels(street_only) == "Your area"
    assert labels({"formatted_address": "Somewhere, Earth", "address_components": []}) == (
        "Your area"
    )
    assert labels({}) == "Your area"
    # Street components are ignored even when a coarser part is present.
    mixed = {
        "formatted_address": "4300 Duval Rd, Austin, TX",
        "address_components": [
            component("4300", "street_number"),
            component("Duval Road", "route"),
            component("Texas", "administrative_area_level_1", short="TX"),
            component("United States", "country", short="US"),
        ],
    }
    label = labels(mixed)
    assert label == "Texas, United States"
    assert "Duval" not in label and "4300" not in label


async def test_a_street_only_result_geocodes_to_a_label_without_street_text() -> None:
    body = {
        "status": "OK",
        "results": [
            {
                "formatted_address": "4300 Duval Rd, Austin, TX 78727, USA",
                "address_components": [component("Duval Road", "route")],
                "geometry": {"location": {"lat": 30.3, "lng": -97.7}},
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    result = await make(handler).geocode("4300 duval rd")
    assert result.label == "Your area"
    assert result.place_id is None


async def test_zero_results_is_geocode_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ZERO_RESULTS", "results": []})

    with pytest.raises(GeocodeNotFound):
        await make(handler).geocode("zzzz")


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(200, json={"status": "OVER_QUERY_LIMIT", "results": []}), "over_quota"),
        (httpx.Response(200, json={"status": "REQUEST_DENIED", "results": []}), "no_key"),
        (httpx.Response(200, json={"status": "UNKNOWN_ERROR"}), "down"),
        (httpx.Response(429, json={}), "over_quota"),
        (httpx.Response(500, text="boom"), "down"),
    ],
)
async def test_failures_are_provider_unavailable_without_leaking_the_key(
    response: httpx.Response, reason: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response

    with pytest.raises(ProviderUnavailable) as info:
        await make(handler).geocode("austin")
    assert (info.value.provider, info.value.reason) == ("google_geocode", reason)
    assert KEY not in str(info.value)


async def test_timeout_is_provider_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    with pytest.raises(ProviderUnavailable) as info:
        await make(handler).geocode("austin")
    assert info.value.reason == "down"


def result_with(**fields: Any) -> dict[str, Any]:
    good = {"geometry": {"location": {"lat": 30.3, "lng": -97.7}}}
    return {"status": "OK", "results": [{**good, **fields}]}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"error": "x"},
        {"status": 5},
        {"status": "OK"},  # `results` is required
        {"status": "OK", "results": []},
        {"status": "OK", "results": "Austin"},  # a string where a list is expected
        {"status": "OK", "results": {}},
        {"status": "OK", "results": [{}]},  # an empty result has no geometry
        {"status": "OK", "results": [{"geometry": {}}]},
        {"status": "OK", "results": [{"geometry": {"location": {}}}]},
        {"status": "OK", "results": [{"geometry": {"location": {"lat": "north", "lng": 1}}}]},
        {"status": "OK", "results": [{"geometry": {"location": {"lat": None, "lng": None}}}]},
        {"status": "OK", "results": ["not a result"]},
        {"status": "OK", "results": [{"geometry": {"location": {"lat": 1}}}]},
        result_with(geometry={"location": {"lat": 91, "lng": 1}}),
        result_with(geometry={"location": {"lat": -90.5, "lng": 1}}),
        result_with(geometry={"location": {"lat": 1, "lng": 181}}),
        result_with(geometry={"location": {"lat": 1, "lng": -180.1}}),
        result_with(geometry={"location": {"lat": float("nan"), "lng": 1}}),
        result_with(geometry={"location": {"lat": "Infinity", "lng": 1}}),
        result_with(geometry={"location": {"lat": 1, "lng": float("-inf")}}),
        result_with(address_components="Austin"),
        result_with(address_components=[{}]),  # a component with no name
        result_with(address_components=[{"long_name": "Austin", "types": "locality"}]),
        result_with(place_id=5),
        ["not", "an", "object"],
        "text",
    ],
)
async def test_malformed_ok_bodies_are_bad_responses(body: Any) -> None:
    with pytest.raises(ProviderUnavailable) as info:
        await make(lambda request: ok_json(body)).geocode("austin")
    assert (info.value.provider, info.value.reason) == ("google_geocode", "bad_response")


async def test_out_of_range_and_non_finite_coordinates_are_never_returned() -> None:
    for lat, lng in [(91, 0), (0, 181), (float("nan"), 0), (0, float("inf"))]:
        body = result_with(geometry={"location": {"lat": lat, "lng": lng}})
        with pytest.raises(ProviderUnavailable) as info:
            await make(lambda request, body=body: ok_json(body)).geocode("austin")
        assert info.value.reason == "bad_response"


async def test_the_run_memo_reuses_a_geocode_within_one_run() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=HYDE_PARK)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    geocoder = GoogleGeocoder(KEY, client=client, runtime=Runtime(memo=RunMemo()))
    first = await geocoder.geocode("Hyde Park")
    assert await geocoder.geocode("  hyde   park ") == first
    assert len(calls) == 1


async def test_the_fake_geocoder_rejects_a_blank_query_like_the_real_one() -> None:
    with pytest.raises(GeocodeNotFound):
        await FakeGeocoder().geocode("   ")
