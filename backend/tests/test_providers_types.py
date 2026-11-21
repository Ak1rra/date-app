"""Shared provider types: distance, opening hours, JSON round-trips."""

from datetime import UTC, date, datetime, time

import pytest

from app.providers.types import (
    Event,
    Forecast,
    HourlyWeather,
    LatLng,
    Place,
    ProviderUnavailable,
    event_from_json,
    forecast_from_json,
    is_open_at,
    place_from_json,
    to_jsonable,
)

AUSTIN = LatLng(30.2672, -97.7431)


def period(day: int, open_h: int, close_day: int, close_h: int) -> dict[str, dict[str, int]]:
    return {
        "open": {"day": day, "hour": open_h, "minute": 0},
        "close": {"day": close_day, "hour": close_h, "minute": 0},
    }


def test_distance_and_offset() -> None:
    assert AUSTIN.distance_m(AUSTIN) == 0
    moved = AUSTIN.offset_m(1000, 0)
    assert moved.lng == AUSTIN.lng
    assert AUSTIN.distance_m(moved) == pytest.approx(1000, rel=0.01)
    east = AUSTIN.offset_m(0, 1000)
    assert AUSTIN.distance_m(east) == pytest.approx(1000, rel=0.01)


def test_is_open_at() -> None:
    friday = [period(5, 9, 5, 22)]  # Fri 09:00-22:00 (day 5, Sunday = 0)
    assert is_open_at(friday, datetime(2026, 11, 20, 20, 0)) is True  # a Friday
    assert is_open_at(friday, datetime(2026, 11, 20, 22, 0)) is False  # closes at 22:00
    assert is_open_at(friday, datetime(2026, 11, 20, 8, 59)) is False
    assert is_open_at(friday, datetime(2026, 11, 21, 12, 0)) is False  # Saturday
    assert is_open_at(None, datetime(2026, 11, 20, 12, 0)) is None


def test_is_open_at_across_midnight_and_the_week_end() -> None:
    bar = [period(6, 16, 0, 2)]  # Saturday 16:00 until Sunday 02:00
    assert is_open_at(bar, datetime(2026, 11, 22, 1, 0)) is True  # Sunday 01:00
    assert is_open_at(bar, datetime(2026, 11, 22, 3, 0)) is False
    assert is_open_at(bar, datetime(2026, 11, 21, 23, 0)) is True  # Saturday 23:00


def test_always_open_has_no_close() -> None:
    always = [{"open": {"day": 0, "hour": 0, "minute": 0}}]
    assert is_open_at(always, datetime(2026, 11, 24, 4, 0)) is True


def test_provider_unavailable_carries_provider_and_reason() -> None:
    error = ProviderUnavailable("google_places", "over_quota")
    assert (error.provider, error.reason) == ("google_places", "over_quota")
    assert "google_places" in str(error)


def test_place_round_trips_through_json() -> None:
    place = Place(
        place_id="p1",
        name="Cafe",
        address="1 Main St",
        location=AUSTIN,
        rating=4.5,
        rating_count=10,
        price_level=2,
        types=["cafe"],
        website=None,
        maps_uri="https://maps.example/p1",
        opening_periods=[period(1, 8, 1, 17)],
        accessibility={"wheelchairAccessibleEntrance": True},
        attributions=["Places data © Google"],
    )
    assert place_from_json(to_jsonable(place)) == place
    no_location = Place(**{**to_jsonable(place), "location": None})
    assert place_from_json(to_jsonable(no_location)) == no_location


def test_event_round_trips_through_json() -> None:
    event = Event(
        event_id="e1",
        name="Show",
        starts_at=datetime(2026, 11, 21, 2, 0, tzinfo=UTC),
        local_date=date(2026, 11, 20),
        local_time=time(20, 0),
        venue_name="Hall",
        venue_address=None,
        location=AUSTIN,
        url="https://tickets.example/e1",
        price_min=10.0,
        price_max=None,
        currency="USD",
        segment="Music",
        genre=None,
    )
    assert event_from_json(to_jsonable(event)) == event
    bare = Event(**{**event.__dict__, "starts_at": None, "local_time": None, "location": None})
    assert event_from_json(to_jsonable(bare)) == bare


def test_forecast_round_trips_through_json() -> None:
    forecast = Forecast(
        hourly=[HourlyWeather(datetime(2026, 11, 20, 18), 14.5, None, 3, 9.0)],
        summary="Overcast",
        risk="low",
        attribution="Weather data by Open-Meteo.com",
    )
    assert forecast_from_json(to_jsonable(forecast)) == forecast
