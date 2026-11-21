"""Helpers for the itinerary tests: a two-member couple, seeding by ORM, fake providers."""

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from app.models import Itinerary, Stop
from app.providers.events.fake import FakeEvents
from app.providers.places.fake import FakePlaces
from app.providers.registry import Providers
from app.providers.routes.fake import FakeRoutes
from app.providers.types import Place, ProviderUnavailable
from app.providers.weather.fake import FakeWeather
from tests.authutil import call, joined_couple

ITINERARIES = "/api/v1/itineraries"

# Demo place ids (providers/places/fake.py); they resolve to fixed coordinates.
TRATTORIA = "demo-place-la-trattoria"
TAQUERIA = "demo-place-taqueria-sol"
GELATO = "demo-place-gelato-lane"

ALLOWED_VENUE_KEYS = {"lat", "lng", "fetched_at"}


def fake_providers(**overrides: Any) -> Providers:
    values: dict[str, Any] = {
        "places": FakePlaces(),
        "events": FakeEvents(),
        "weather": FakeWeather(),
        "routes": FakeRoutes(),
        "geocoder": None,
        "memo": None,
    }
    values.update(overrides)
    return Providers(**values)


class DownPlaces:
    async def search(self, *args: Any, **kwargs: Any) -> list[Place]:
        raise ProviderUnavailable("places", "down")

    async def details(self, place_id: str) -> Place:
        raise ProviderUnavailable("places", "down")


class DownRoutes:
    async def travel(self, *args: Any, **kwargs: Any) -> Any:
        raise ProviderUnavailable("routes", "down")


class CountingRoutes(FakeRoutes):
    """FakeRoutes that records its calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def travel(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(args)
        return await super().travel(*args, **kwargs)


class CountingPlaces(FakePlaces):
    """FakePlaces that records its calls."""

    def __init__(self) -> None:
        self.details_calls: list[str] = []
        self.search_calls: list[dict[str, Any]] = []

    async def details(self, place_id: str) -> Place:
        self.details_calls.append(place_id)
        return await super().details(place_id)

    async def search(self, query: str, near: Any, radius_m: int, **kwargs: Any) -> list[Place]:
        self.search_calls.append({"query": query, "radius_m": radius_m, **kwargs})
        return await super().search(query, near, radius_m, **kwargs)


@dataclass
class World:
    app: FastAPI
    owner: AsyncClient
    partner: AsyncClient
    owner_id: uuid.UUID
    partner_id: uuid.UUID
    couple_id: uuid.UUID
    providers: dict[str, Providers]


@asynccontextmanager
async def world(app: FastAPI) -> AsyncIterator[World]:
    """Two signed-in members of one couple, with fake providers swapped in for the routes."""
    from app.api.v1.itineraries import get_itinerary_providers

    holder = {"p": fake_providers()}
    app.dependency_overrides[get_itinerary_providers] = lambda: holder["p"]
    try:
        async with joined_couple(app) as (owner, partner):
            me = (await owner.get("/api/v1/me")).json()
            other = (await partner.get("/api/v1/me")).json()
            yield World(
                app=app,
                owner=owner,
                partner=partner,
                owner_id=uuid.UUID(me["id"]),
                partner_id=uuid.UUID(other["id"]),
                couple_id=uuid.UUID(me["couple_id"]),
                providers=holder,
            )
    finally:
        app.dependency_overrides.pop(get_itinerary_providers, None)


def when(days: int = 10, hour: int = 18) -> dt.datetime:
    base = dt.datetime.now(dt.UTC) + dt.timedelta(days=days)
    return base.replace(hour=hour, minute=0, second=0, microsecond=0)


async def seed(
    w: World,
    *,
    planner: str = "owner",
    couple_id: uuid.UUID | None = None,
    stops: list[dict[str, Any]] | None = None,
    **fields: Any,
) -> uuid.UUID:
    """An itinerary (and stops) inserted straight through the ORM; returns its id."""
    values: dict[str, Any] = {"title": "Date night", "status": "draft"}
    values.update(fields)
    async with w.app.state.sessionmaker() as session:
        itinerary = Itinerary(
            couple_id=couple_id or w.couple_id,
            planner_user_id=w.owner_id if planner == "owner" else w.partner_id,
            **values,
        )
        session.add(itinerary)
        await session.flush()
        for index, stop in enumerate(stops or []):
            session.add(Stop(itinerary_id=itinerary.id, position=index, **stop))
        await session.commit()
        return itinerary.id


async def set_home(w: World) -> None:
    response = await call(w.owner, "PUT", "/api/v1/couple/home-area", {"query": "Austin"})
    assert response.status_code == 200, response.text
