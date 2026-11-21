"""Deterministic events for DEMO_MODE and tests: a few per requested day, no network."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from app.providers.types import Event, LatLng

MAX_DAYS = 14
PER_DAY = 3


@dataclass(frozen=True)
class _Template:
    slug: str
    name: str
    segment: str
    genre: str
    venue: str
    at: time
    price: float | None  # None = free


TEMPLATES: tuple[_Template, ...] = (
    _Template("jazz-night", "Jazz Night", "Music", "Jazz", "Blue Door Club", time(20, 0), 20.0),
    _Template(
        "indie-showcase", "Indie Showcase", "Music", "Rock", "The Hideout", time(20, 30), 15.0
    ),
    _Template(
        "park-concert", "Concert in the Park", "Music", "Pop", "Riverside Park", time(18, 0), None
    ),
    _Template(
        "comedy-hour",
        "Stand-Up Comedy Hour",
        "Arts & Theatre",
        "Comedy",
        "Laugh Lab",
        time(19, 30),
        18.0,
    ),
    _Template(
        "community-play",
        "Community Theatre: A Midsummer Night",
        "Arts & Theatre",
        "Theatre",
        "Playhouse 9",
        time(19, 0),
        25.0,
    ),
    _Template(
        "farmers-market",
        "Saturday Farmers Market",
        "Miscellaneous",
        "Market",
        "Old Mill Square",
        time(10, 0),
        None,
    ),
    _Template(
        "outdoor-movie",
        "Outdoor Movie Night",
        "Film",
        "Family",
        "Greenbelt Lawn",
        time(20, 0),
        None,
    ),
    _Template(
        "food-truck-fest",
        "Food Truck Festival",
        "Miscellaneous",
        "Food",
        "Fairground Lot",
        time(12, 0),
        5.0,
    ),
    _Template(
        "art-walk",
        "Gallery Art Walk",
        "Arts & Theatre",
        "Fine Art",
        "Arts District",
        time(17, 30),
        None,
    ),
    _Template(
        "minor-league",
        "Minor League Baseball Night",
        "Sports",
        "Baseball",
        "Capitol Field",
        time(19, 5),
        12.0,
    ),
)


def _digest(*parts: str) -> bytes:
    return hashlib.sha256("|".join(parts).encode()).digest()


def _event(template: _Template, day: date, near: LatLng) -> Event:
    digest = _digest(template.slug)
    north = (digest[0] / 255 * 2 - 1) * 2000
    east = (digest[1] / 255 * 2 - 1) * 2000
    return Event(
        event_id=f"demo-event-{day.isoformat()}-{template.slug}",
        name=template.name,
        starts_at=datetime.combine(day, template.at, tzinfo=UTC),
        local_date=day,
        local_time=template.at,
        venue_name=template.venue,
        venue_address=f"{200 + digest[2] * 2} Demo Avenue, Austin, TX",
        location=near.offset_m(north, east),
        url=f"https://example.com/events/{template.slug}",
        price_min=template.price,
        price_max=None if template.price is None else template.price * 2,
        currency="USD" if template.price is not None else None,
        segment=template.segment,
        genre=template.genre,
    )


def _utc(moment: datetime) -> datetime:
    """UTC if timezone-aware, else read as UTC (the EventsProvider contract)."""
    return moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)


class FakeEvents:
    """Deterministic EventsProvider: PER_DAY events on each date from `start` to `end` (at most
    MAX_DAYS), picked by hashing the date. Venues sit within about 3 km of `near`; only events
    that start in [start, end) and lie within `radius_km` of `near` are returned."""

    async def search(
        self,
        near: LatLng,
        radius_km: int,
        start: datetime,
        end: datetime,
        *,
        keywords: str | None = None,
        segment: str | None = None,
        max_results: int = 10,
    ) -> list[Event]:
        if max_results <= 0:
            return []
        start, end = _utc(start), _utc(end)
        found: list[Event] = []
        day = start.date()
        last = min(end.date(), start.date() + timedelta(days=MAX_DAYS - 1))
        while day <= last:
            order = sorted(TEMPLATES, key=lambda t: _digest(day.isoformat(), t.slug))
            found.extend(_event(t, day, near) for t in order[:PER_DAY])
            day += timedelta(days=1)
        found = [
            e
            for e in found
            if e.starts_at is not None
            and start <= e.starts_at < end
            and (e.location is None or near.distance_m(e.location) <= radius_km * 1000)
        ]
        if keywords:
            words = keywords.lower().split()
            found = [
                e
                for e in found
                if any(w in f"{e.name} {e.genre} {e.segment} {e.venue_name}".lower() for w in words)
            ]
        if segment:
            found = [e for e in found if (e.segment or "").lower() == segment.lower()]
        found.sort(key=lambda e: (e.local_date, e.local_time or time(0), e.event_id))
        return found[:max_results]
