"""Provider selection: a real provider if its key is set, else a fake in DEMO_MODE, else none."""

import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.errors import ApiError
from app.providers.cache import RunMemo
from app.providers.events.fake import FakeEvents
from app.providers.events.ticketmaster import TicketmasterEvents
from app.providers.geocoding.fake import FakeGeocoder
from app.providers.geocoding.google import GoogleGeocoder
from app.providers.places.fake import FakePlaces
from app.providers.places.google import GooglePlaces
from app.providers.ratelimit import shared_limiter
from app.providers.registry import (
    Providers,
    build_providers,
    get_events,
    get_geocoder,
    get_places,
    get_routes,
    get_weather,
)
from app.providers.routes.fake import FakeRoutes
from app.providers.routes.google import GoogleRoutes
from app.providers.types import LatLng, ProviderUnavailable
from app.providers.weather.fake import FakeWeather
from app.providers.weather.open_meteo import OpenMeteoWeather
from tests.conftest import make_settings

KEYS = {"google_maps_api_key": "g", "ticketmaster_api_key": "t"}
SESSIONMAKER = object()
REQUEST: Any = SimpleNamespace(
    app=SimpleNamespace(state=SimpleNamespace(sessionmaker=SESSIONMAKER))
)


def test_without_keys_or_demo_mode_everything_but_weather_is_unavailable() -> None:
    providers = build_providers(make_settings(demo_mode=False))
    assert isinstance(providers, Providers)
    assert providers.places is None
    assert providers.events is None
    assert providers.routes is None
    assert providers.geocoder is None
    assert isinstance(providers.weather, OpenMeteoWeather)  # keyless, so always available


def test_demo_mode_without_keys_uses_the_fakes() -> None:
    providers = build_providers(make_settings(demo_mode=True))
    assert isinstance(providers.places, FakePlaces)
    assert isinstance(providers.events, FakeEvents)
    assert isinstance(providers.routes, FakeRoutes)
    assert isinstance(providers.geocoder, FakeGeocoder)
    assert isinstance(providers.weather, OpenMeteoWeather)  # weather is real unless asked otherwise


@pytest.mark.parametrize("demo_mode", [False, True])
def test_keys_select_the_real_providers_with_or_without_demo_mode(demo_mode: bool) -> None:
    providers = build_providers(make_settings(demo_mode=demo_mode, **KEYS))
    assert isinstance(providers.places, GooglePlaces)
    assert isinstance(providers.events, TicketmasterEvents)
    assert isinstance(providers.routes, GoogleRoutes)
    assert isinstance(providers.geocoder, GoogleGeocoder)


def test_each_key_only_switches_its_own_providers() -> None:
    only_tm = build_providers(make_settings(demo_mode=True, ticketmaster_api_key="t"))
    assert isinstance(only_tm.events, TicketmasterEvents)
    assert isinstance(only_tm.places, FakePlaces)
    only_google = build_providers(make_settings(demo_mode=False, google_maps_api_key="g"))
    assert isinstance(only_google.places, GooglePlaces)
    assert only_google.events is None


def test_weather_provider_setting() -> None:
    assert isinstance(build_providers(make_settings(weather_provider="fake")).weather, FakeWeather)
    assert isinstance(
        build_providers(make_settings(weather_provider="open-meteo")).weather, OpenMeteoWeather
    )
    assert isinstance(
        build_providers(make_settings(demo_mode=False, weather_provider="fake")).weather,
        FakeWeather,
    )


def test_build_providers_charges_the_couple_and_uses_the_settings_ttls() -> None:
    couple = uuid.uuid4()
    settings = make_settings(demo_mode=False, cache_ttl_events=22, cache_ttl_weather=33, **KEYS)
    providers = build_providers(settings, couple_id=couple, session_factory=SESSIONMAKER)  # type: ignore[arg-type]
    assert isinstance(providers.places, GooglePlaces) and isinstance(
        providers.events, TicketmasterEvents
    )
    assert isinstance(providers.weather, OpenMeteoWeather) and isinstance(
        providers.routes, GoogleRoutes
    )
    for provider in (providers.places, providers.events, providers.weather, providers.routes):
        assert provider._runtime.couple_id == couple
        assert provider._runtime.session_factory is SESSIONMAKER
        assert provider._runtime.limiter is not None
    # Only the providers whose terms allow it have a database cache TTL.
    assert (providers.events._ttl, providers.weather._ttl) == (22, 33)


def test_a_bundle_carries_one_run_memo_for_its_google_providers() -> None:
    providers = build_providers(make_settings(demo_mode=False, **KEYS))
    assert providers.memo is not None and len(providers.memo) == 0
    google = (providers.places, providers.routes, providers.geocoder)
    assert all(g._runtime.memo is providers.memo for g in google)  # type: ignore[union-attr]
    # The memo is only for the Google providers; the others keep the database cache.
    assert providers.events._runtime.memo is None  # type: ignore[union-attr]
    assert providers.weather._runtime.memo is None  # type: ignore[attr-defined]
    # Each bundle (each run) has its own memo, so nothing is shared across runs.
    assert build_providers(make_settings(demo_mode=False, **KEYS)).memo is not providers.memo
    mine = RunMemo()
    assert build_providers(make_settings(**KEYS), memo=mine).memo is mine


def current(couple_id: uuid.UUID | None) -> Any:
    return SimpleNamespace(couple_id=couple_id)


def test_the_dependencies_follow_the_same_rule() -> None:
    demo = make_settings(demo_mode=True)
    me = current(None)
    assert isinstance(get_places(REQUEST, demo, me), FakePlaces)
    assert isinstance(get_events(REQUEST, demo, me), FakeEvents)
    assert isinstance(get_routes(REQUEST, demo, me), FakeRoutes)
    assert isinstance(get_geocoder(demo, me), FakeGeocoder)
    assert isinstance(get_weather(REQUEST, demo, me), OpenMeteoWeather)
    keyed = make_settings(demo_mode=True, **KEYS)
    assert isinstance(get_places(REQUEST, keyed, me), GooglePlaces)
    assert get_places(REQUEST, keyed, me)._runtime.session_factory is SESSIONMAKER  # the app's own
    assert isinstance(get_geocoder(keyed, me), GoogleGeocoder)  # a key wins over DEMO_MODE


def test_the_dependencies_charge_the_current_users_couple() -> None:
    couple = uuid.uuid4()
    keyed = make_settings(demo_mode=False, **KEYS)
    me = current(couple)
    real = (
        get_places(REQUEST, keyed, me),
        get_events(REQUEST, keyed, me),
        get_routes(REQUEST, keyed, me),
        get_weather(REQUEST, keyed, me),
        get_geocoder(keyed, me),
    )
    for provider in real:
        assert provider._runtime.couple_id == couple  # type: ignore[attr-defined]


async def test_the_per_couple_limit_trips_through_the_dependencies() -> None:
    settings = make_settings(
        demo_mode=False,
        provider_calls_per_couple_per_minute=2,
        provider_calls_global_per_minute=999,
        **KEYS,
    )
    limiter = shared_limiter(settings)
    limiter.reset()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"places": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    couple, other = uuid.uuid4(), uuid.uuid4()
    near = LatLng(47.6, -122.3)

    async def search(who: uuid.UUID, query: str) -> None:
        places = get_places(REQUEST, settings, current(who))
        places._client = client  # type: ignore[attr-defined]
        await places.search(query, near, 1000)

    await search(couple, "a")
    await search(couple, "b")
    with pytest.raises(ProviderUnavailable) as info:
        await search(couple, "c")  # a third call within the minute, from the same couple
    assert info.value.reason == "rate_limited"
    assert len(calls) == 2  # the third never went out
    await search(other, "a")  # another couple is not affected
    assert len(calls) == 3
    limiter.reset()


@pytest.mark.parametrize(
    ("dependency", "message"),
    [
        (get_places, "Place search isn't available right now."),
        (get_events, "Event search isn't available right now."),
        (get_routes, "Travel times aren't available right now."),
    ],
)
def test_unavailable_dependencies_are_a_503(dependency: Any, message: str) -> None:
    with pytest.raises(ApiError) as info:
        dependency(REQUEST, make_settings(demo_mode=False), current(None))
    assert (info.value.status, info.value.code, info.value.message) == (
        503,
        "provider_unavailable",
        message,
    )


def test_the_fake_geocoder_setting_yields_to_a_key() -> None:
    assert make_settings(demo_mode=True).use_fake_geocoder is True
    assert make_settings(demo_mode=True, google_maps_api_key="g").use_fake_geocoder is False
    assert make_settings(demo_mode=False).use_fake_geocoder is False
