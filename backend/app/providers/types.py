"""Shared provider types (design: m2-ai-plan.md, "Providers").

Times that describe a place (opening hours, `open_at`, a weather window, event times) are local
wall-clock times at that place: the providers read their hour/minute/weekday fields and never
convert time zones.
"""

import math
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime, time
from typing import Any, Literal

# ProviderUnavailable.reason values
DOWN = "down"
OVER_QUOTA = "over_quota"
NO_KEY = "no_key"
RATE_LIMITED = "rate_limited"
NOT_FOUND = "not_found"  # the provider is up but has nothing for this id or route
BAD_RESPONSE = "bad_response"  # the provider answered 2xx with a body we cannot read


class ProviderUnavailable(Exception):
    """A provider could not answer: down, over quota, key missing or rejected, rate limited by
    us, or (reason "not_found") nothing exists for the request. Never carries a URL or a body,
    because some request URLs hold an API key."""

    def __init__(self, provider: str, reason: str) -> None:
        super().__init__(f"{provider} unavailable: {reason}")
        self.provider = provider
        self.reason = reason


@dataclass(frozen=True)
class LatLng:
    lat: float
    lng: float

    def distance_m(self, other: "LatLng") -> float:
        """Great-circle (haversine) distance in metres."""
        phi1, phi2 = math.radians(self.lat), math.radians(other.lat)
        d_phi = phi2 - phi1
        d_lambda = math.radians(other.lng - self.lng)
        a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return 2 * 6_371_000 * math.asin(math.sqrt(a))

    def offset_m(self, north_m: float, east_m: float) -> "LatLng":
        """A point `north_m` north and `east_m` east of this one (small distances)."""
        dlat = north_m / 111_320
        dlng = east_m / (111_320 * max(math.cos(math.radians(self.lat)), 0.01))
        return LatLng(round(self.lat + dlat, 6), round(self.lng + dlng, 6))


@dataclass(frozen=True)
class Place:
    place_id: str
    name: str
    address: str | None
    location: LatLng | None
    rating: float | None
    rating_count: int | None
    price_level: int | None  # 0..4
    types: list[str]
    website: str | None
    maps_uri: str | None
    # [{"open": {"day": 0-6 (Sun=0), "hour", "minute"}, "close": {...}}]; None = unknown
    opening_periods: list[dict[str, Any]] | None
    accessibility: dict[str, bool] = field(default_factory=dict)
    attributions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Event:
    event_id: str
    name: str
    starts_at: datetime | None
    local_date: date
    local_time: time | None
    venue_name: str | None
    venue_address: str | None
    location: LatLng | None
    url: str | None
    price_min: float | None
    price_max: float | None
    currency: str | None
    segment: str | None
    genre: str | None


@dataclass(frozen=True)
class HourlyWeather:
    time: datetime
    temp_c: float
    precip_prob: int | None
    weather_code: int
    wind_kmh: float | None


@dataclass(frozen=True)
class Forecast:
    hourly: list[HourlyWeather]
    summary: str
    risk: Literal["none", "low", "meaningful"]
    attribution: str


@dataclass(frozen=True)
class TravelLegEstimate:
    mode: str
    duration_minutes: int
    distance_m: int | None


def is_open_at(periods: list[dict[str, Any]] | None, when: datetime) -> bool | None:
    """Whether Google-shaped `periods` cover the wall-clock time `when`; None if unknown.

    A period with no `close` (an always-open place) covers every time.
    """
    if periods is None:
        return None
    week = 7 * 24 * 60
    now = ((when.weekday() + 1) % 7) * 1440 + when.hour * 60 + when.minute  # Sunday = 0
    for period in periods:
        opening = period["open"]
        start = opening["day"] * 1440 + opening["hour"] * 60 + opening.get("minute", 0)
        closing = period.get("close")
        if closing is None:
            return True
        end = closing["day"] * 1440 + closing["hour"] * 60 + closing.get("minute", 0)
        if end <= start:
            end += week  # wraps past Saturday night into Sunday
        if start <= now < end or start <= now + week < end:
            return True
    return False


def to_jsonable(value: Any) -> Any:
    """Dataclasses, datetimes, dates and times as plain JSON values (for the cache)."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    return value


def _latlng(data: dict[str, Any] | None) -> LatLng | None:
    return LatLng(**data) if data else None


def place_from_json(data: dict[str, Any]) -> Place:
    return Place(**{**data, "location": _latlng(data.get("location"))})


def _optional(parse: Any, value: str | None) -> Any:
    return parse(value) if value else None


def event_from_json(data: dict[str, Any]) -> Event:
    return Event(
        **{
            **data,
            "starts_at": _optional(datetime.fromisoformat, data.get("starts_at")),
            "local_date": date.fromisoformat(data["local_date"]),
            "local_time": _optional(time.fromisoformat, data.get("local_time")),
            "location": _latlng(data.get("location")),
        }
    )


def forecast_from_json(data: dict[str, Any]) -> Forecast:
    hourly = [
        HourlyWeather(**{**h, "time": datetime.fromisoformat(h["time"])}) for h in data["hourly"]
    ]
    return Forecast(**{**data, "hourly": hourly})


def leg_from_json(data: dict[str, Any]) -> TravelLegEstimate:
    return TravelLegEstimate(**data)
