"""Picks provider implementations from Settings. The `get_*` functions are FastAPI dependencies,
so tests swap them with `app.dependency_overrides`.

Selection rule (INT-1, INT-3): a provider uses its real API when its key is set; without a key
it uses the deterministic fake when DEMO_MODE is on, and otherwise it is unavailable. Weather
needs no key: Open-Meteo unless WEATHER_PROVIDER=fake.
"""

import uuid
from dataclasses import dataclass
from functools import lru_cache

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import make_engine, make_sessionmaker
from app.deps import CurrentUser, current_user, get_app_settings
from app.errors import ApiError
from app.providers.cache import RunMemo
from app.providers.email.base import EmailSender
from app.providers.email.console import ConsoleEmailSender
from app.providers.email.resend import ResendEmailSender
from app.providers.events.base import EventsProvider
from app.providers.events.fake import FakeEvents
from app.providers.events.ticketmaster import TicketmasterEvents
from app.providers.geocoding.base import Geocoder
from app.providers.geocoding.fake import FakeGeocoder
from app.providers.geocoding.google import GoogleGeocoder
from app.providers.http import Runtime
from app.providers.places.base import PlacesProvider
from app.providers.places.fake import FakePlaces
from app.providers.places.google import GooglePlaces
from app.providers.ratelimit import shared_limiter
from app.providers.routes.base import RoutesProvider
from app.providers.routes.fake import FakeRoutes
from app.providers.routes.google import GoogleRoutes
from app.providers.weather.base import WeatherProvider
from app.providers.weather.fake import FakeWeather
from app.providers.weather.open_meteo import OpenMeteoWeather

SessionFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class Providers:
    """The provider bundle for code that runs outside a request handler (the AI layer, the
    worker). A field is None when that provider is unavailable (no key and not DEMO_MODE): the
    caller reports it as down and carries on, as the AI tools do. A real provider that is
    available but fails raises ProviderUnavailable when used instead. `memo` is the in-memory
    store the Google providers reuse within one run; it is never persisted or shared."""

    places: PlacesProvider | None
    events: EventsProvider | None
    weather: WeatherProvider | None
    routes: RoutesProvider | None
    geocoder: Geocoder | None
    memo: RunMemo | None = None


@lru_cache
def _default_session_factory(database_url: str) -> SessionFactory:
    return make_sessionmaker(make_engine(Settings(_env_file=None, database_url=database_url)))


def _runtime(
    settings: Settings,
    couple_id: uuid.UUID | None,
    session_factory: SessionFactory | None,
    memo: RunMemo | None = None,
) -> Runtime:
    """Cache, rate limit and run memo for a real provider. With no session factory, the
    process-wide engine for DATABASE_URL (created on first use)."""
    return Runtime(
        session_factory=session_factory or _default_session_factory(settings.database_url),
        limiter=shared_limiter(settings),
        couple_id=couple_id,
        memo=memo,
    )


def build_places(
    settings: Settings,
    *,
    couple_id: uuid.UUID | None = None,
    session_factory: SessionFactory | None = None,
    memo: RunMemo | None = None,
) -> PlacesProvider | None:
    if settings.google_maps_api_key:
        return GooglePlaces(
            settings.google_maps_api_key,
            runtime=_runtime(settings, couple_id, session_factory, memo),
        )
    return FakePlaces() if settings.demo_mode else None


def build_events(
    settings: Settings,
    *,
    couple_id: uuid.UUID | None = None,
    session_factory: SessionFactory | None = None,
) -> EventsProvider | None:
    if settings.ticketmaster_api_key:
        return TicketmasterEvents(
            settings.ticketmaster_api_key,
            ttl=settings.cache_ttl_events,
            runtime=_runtime(settings, couple_id, session_factory),
        )
    return FakeEvents() if settings.demo_mode else None


def build_weather(
    settings: Settings,
    *,
    couple_id: uuid.UUID | None = None,
    session_factory: SessionFactory | None = None,
) -> WeatherProvider:
    if settings.weather_provider == "fake":
        return FakeWeather()
    return OpenMeteoWeather(
        ttl=settings.cache_ttl_weather, runtime=_runtime(settings, couple_id, session_factory)
    )


def build_routes(
    settings: Settings,
    *,
    couple_id: uuid.UUID | None = None,
    session_factory: SessionFactory | None = None,
    memo: RunMemo | None = None,
) -> RoutesProvider | None:
    if settings.google_maps_api_key:
        return GoogleRoutes(
            settings.google_maps_api_key,
            runtime=_runtime(settings, couple_id, session_factory, memo),
        )
    return FakeRoutes() if settings.demo_mode else None


def build_geocoder(
    settings: Settings,
    *,
    couple_id: uuid.UUID | None = None,
    session_factory: SessionFactory | None = None,
    memo: RunMemo | None = None,
) -> Geocoder | None:
    if settings.google_maps_api_key:
        return GoogleGeocoder(
            settings.google_maps_api_key,
            runtime=_runtime(settings, couple_id, session_factory, memo),
        )
    return FakeGeocoder() if settings.use_fake_geocoder else None


def build_providers(
    settings: Settings,
    *,
    couple_id: uuid.UUID | None = None,
    session_factory: SessionFactory | None = None,
    memo: RunMemo | None = None,
) -> Providers:
    """Every provider for one couple's AI run or a background job. `couple_id` charges the
    per-couple rate limit; `session_factory` (the app's `request.app.state.sessionmaker`, say)
    is where Ticketmaster and Open-Meteo responses are cached, defaulting to a process-wide
    engine for DATABASE_URL. Google responses are never stored: the bundle gets a fresh RunMemo
    (or the given one) that its Google providers reuse, so build one bundle per run."""
    memo = RunMemo() if memo is None else memo
    return Providers(
        places=build_places(
            settings, couple_id=couple_id, session_factory=session_factory, memo=memo
        ),
        events=build_events(settings, couple_id=couple_id, session_factory=session_factory),
        weather=build_weather(settings, couple_id=couple_id, session_factory=session_factory),
        routes=build_routes(
            settings, couple_id=couple_id, session_factory=session_factory, memo=memo
        ),
        geocoder=build_geocoder(
            settings, couple_id=couple_id, session_factory=session_factory, memo=memo
        ),
        memo=memo,
    )


def get_email_sender(settings: Settings = Depends(get_app_settings)) -> EmailSender:
    if settings.effective_email_provider == "console":
        return ConsoleEmailSender()
    if not settings.email_api_key or not settings.email_from:
        raise ApiError(503, "provider_unavailable", "Email isn't available right now.")
    return ResendEmailSender(settings.email_api_key, settings.email_from)


def get_geocoder(
    settings: Settings = Depends(get_app_settings), current: CurrentUser = Depends(current_user)
) -> Geocoder:
    geocoder = build_geocoder(settings, couple_id=current.couple_id)
    if geocoder is None:
        raise ApiError(503, "provider_unavailable", "Location lookup isn't available right now.")
    return geocoder


def get_places(
    request: Request,
    settings: Settings = Depends(get_app_settings),
    current: CurrentUser = Depends(current_user),
) -> PlacesProvider:
    places = build_places(
        settings, couple_id=current.couple_id, session_factory=request.app.state.sessionmaker
    )
    if places is None:
        raise ApiError(503, "provider_unavailable", "Place search isn't available right now.")
    return places


def get_events(
    request: Request,
    settings: Settings = Depends(get_app_settings),
    current: CurrentUser = Depends(current_user),
) -> EventsProvider:
    events = build_events(
        settings, couple_id=current.couple_id, session_factory=request.app.state.sessionmaker
    )
    if events is None:
        raise ApiError(503, "provider_unavailable", "Event search isn't available right now.")
    return events


def get_weather(
    request: Request,
    settings: Settings = Depends(get_app_settings),
    current: CurrentUser = Depends(current_user),
) -> WeatherProvider:
    return build_weather(
        settings, couple_id=current.couple_id, session_factory=request.app.state.sessionmaker
    )


def get_routes(
    request: Request,
    settings: Settings = Depends(get_app_settings),
    current: CurrentUser = Depends(current_user),
) -> RoutesProvider:
    routes = build_routes(
        settings, couple_id=current.couple_id, session_factory=request.app.state.sessionmaker
    )
    if routes is None:
        raise ApiError(503, "provider_unavailable", "Travel times aren't available right now.")
    return routes
