"""Ticketmaster Discovery API v2 (events.json). The API key is a query parameter, so request
URLs are never logged (see RealProvider._json)."""

from datetime import UTC, datetime

import httpx

from app.providers.events.schema import (
    Classification,
    EventsResponse,
    Price,
    TicketmasterEvent,
    Venue,
)
from app.providers.http import RealProvider, Runtime
from app.providers.types import Event, LatLng, event_from_json, to_jsonable

EVENTS_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash(point: LatLng, precision: int = 9) -> str:
    """Standard geohash, for the `geoPoint` parameter."""
    lat_range, lng_range = [-90.0, 90.0], [-180.0, 180.0]
    chars: list[str] = []
    bits = bit_count = 0
    use_lng = True
    while len(chars) < precision:
        value, rng = (point.lng, lng_range) if use_lng else (point.lat, lat_range)
        mid = (rng[0] + rng[1]) / 2
        bits <<= 1
        if value >= mid:
            bits |= 1
            rng[0] = mid
        else:
            rng[1] = mid
        use_lng = not use_lng
        bit_count += 1
        if bit_count == 5:
            chars.append(_BASE32[bits])
            bits = bit_count = 0
    return "".join(chars)


def _utc_z(moment: datetime) -> str:
    """Ticketmaster wants ISO 8601 UTC with a Z and no fraction."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    return moment.replace(tzinfo=None, microsecond=0).isoformat() + "Z"


def event_from_ticketmaster(data: TicketmasterEvent) -> Event | None:
    """None when the event has no local date (it cannot be placed in a plan)."""
    start = data.dates.start if data.dates else None
    if start is None or start.localDate is None:
        return None
    venues = data.embedded.venues if data.embedded else None
    venue = venues[0] if venues else Venue()
    address = ", ".join(
        part
        for part in (
            venue.address.line1 if venue.address else None,
            venue.city.name if venue.city else None,
            venue.state.stateCode if venue.state else None,
        )
        if part
    )
    price = data.priceRanges[0] if data.priceRanges else Price()
    classification = data.classifications[0] if data.classifications else Classification()
    return Event(
        event_id=data.id,
        name=data.name or "",
        starts_at=start.dateTime,
        local_date=start.localDate,
        local_time=start.localTime,
        venue_name=venue.name,
        venue_address=address or None,
        location=(
            LatLng(venue.location.latitude, venue.location.longitude) if venue.location else None
        ),
        url=data.url,
        price_min=price.min,
        price_max=price.max,
        currency=price.currency,
        segment=classification.segment.name if classification.segment else None,
        genre=classification.genre.name if classification.genre else None,
    )


class TicketmasterEvents(RealProvider):
    provider = "ticketmaster"

    def __init__(
        self,
        api_key: str,
        *,
        ttl: int = 21600,
        client: httpx.AsyncClient | None = None,
        runtime: Runtime | None = None,
    ) -> None:
        super().__init__(client=client, runtime=runtime)
        self._api_key = api_key
        self._ttl = ttl

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
        params: dict[str, str | int] = {
            "geoPoint": geohash(near),
            "radius": max(1, radius_km),
            "unit": "km",
            "startDateTime": _utc_z(start),
            "endDateTime": _utc_z(end),
            "sort": "date,asc",
            "size": max(1, min(max_results, 50)),
        }
        if keywords:
            params["keyword"] = keywords
        if segment:
            params["segmentName"] = segment

        async def fetch() -> list[Event]:
            data = await self._json("GET", EVENTS_URL, params={**params, "apikey": self._api_key})
            response = EventsResponse.model_validate(data)
            events = response.embedded.events if response.embedded else []
            parsed = [e for e in map(event_from_ticketmaster, events) if e is not None]
            return parsed[:max_results]

        # The key is not part of the cache key.
        return await self._cached(
            {"op": "search", "near": near, "params": {**params, "geoPoint": None}},
            self._ttl,
            fetch,
            encode=to_jsonable,
            decode=lambda payload: [event_from_json(e) for e in payload],
        )
