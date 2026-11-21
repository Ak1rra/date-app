"""Google Places API (New): Text Search and Place Details.

Field masks are mandatory. Text Search has no "open at time T" filter (only `openNow`), so
`open_at` is applied client-side to the returned `regularOpeningHours.periods`. Its
`locationRestriction` is rectangle-only, so the search area is the circle's bounding box and the
exact radius is applied client-side. So is the price filter, because the API's `priceLevels`
cannot select free places. Google's terms forbid storing its responses: results are only
memoized for the length of one run (see RunMemo).
"""

import logging
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from app.providers.http import RealProvider, Runtime
from app.providers.places.schema import GooglePlace, OpeningHours, Point, SearchResponse
from app.providers.types import (
    LatLng,
    Place,
    is_open_at,
)

logger = logging.getLogger("app.providers")

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/"
ATTRIBUTION = "Places data © Google"
MAX_RADIUS_M = 50_000

_FIELDS = (
    "id,displayName,formattedAddress,location,rating,userRatingCount,priceLevel,"
    "regularOpeningHours,websiteUri,googleMapsUri,accessibilityOptions,types"
)
SEARCH_FIELD_MASK = ",".join(f"places.{name}" for name in _FIELDS.split(","))
DETAILS_FIELD_MASK = _FIELDS

# PRICE_LEVEL_FREE is response-only; UNSPECIFIED means unknown.
PRICE_FROM_ENUM = {
    "PRICE_LEVEL_FREE": 0,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}
PRICE_TO_ENUM = {level: enum for enum, level in PRICE_FROM_ENUM.items() if level > 0}


def place_from_google(data: GooglePlace) -> Place:
    return Place(
        place_id=data.id,
        name=data.displayName.text if data.displayName else "",
        address=data.formattedAddress,
        location=(
            LatLng(data.location.latitude, data.location.longitude) if data.location else None
        ),
        rating=data.rating,
        rating_count=data.userRatingCount,
        price_level=PRICE_FROM_ENUM.get(data.priceLevel or ""),
        types=data.types,
        website=data.websiteUri,
        maps_uri=data.googleMapsUri,
        opening_periods=_periods(data.regularOpeningHours),
        accessibility=dict(data.accessibilityOptions or {}),
        attributions=[ATTRIBUTION],
    )


def _periods(hours: OpeningHours | None) -> list[dict[str, Any]] | None:
    if hours is None or hours.periods is None:
        return None
    return [
        {"open": _point(period.open)} | ({"close": _point(period.close)} if period.close else {})
        for period in hours.periods
    ]


def _point(point: Point) -> dict[str, int]:
    return {"day": point.day, "hour": point.hour, "minute": point.minute}


def _only_location_errors(error: ValidationError) -> bool:
    """Whether every problem with a place is in its coordinates."""
    return all(e["loc"] and e["loc"][0] == "location" for e in error.errors())


class GooglePlaces(RealProvider):
    provider = "google_places"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        runtime: Runtime | None = None,
    ) -> None:
        super().__init__(client=client, runtime=runtime)
        self._api_key = api_key

    def _headers(self, field_mask: str) -> dict[str, str]:
        return {"X-Goog-Api-Key": self._api_key, "X-Goog-FieldMask": field_mask}

    async def search(
        self,
        query: str,
        near: LatLng,
        radius_m: int,
        *,
        included_type: str | None = None,
        price_levels: list[int] | None = None,
        open_at: datetime | None = None,
        max_results: int = 8,
    ) -> list[Place]:
        if max_results <= 0:
            return []
        radius = max(1, min(radius_m, MAX_RADIUS_M))
        low, high = near.offset_m(-radius, -radius), near.offset_m(radius, radius)
        body: dict[str, Any] = {
            "textQuery": query,
            "pageSize": 20,  # always a full page: the filters below can thin it out
            "locationRestriction": {
                "rectangle": {
                    "low": {"latitude": low.lat, "longitude": low.lng},
                    "high": {"latitude": high.lat, "longitude": high.lng},
                }
            },
        }
        if included_type:
            body["includedType"] = included_type
            body["strictTypeFiltering"] = True
        wanted = set(price_levels or [])
        levels = [PRICE_TO_ENUM[p] for p in sorted(wanted) if p in PRICE_TO_ENUM]
        # Free is not an accepted filter value, so asking for it means not filtering by price in
        # the request: the paid levels alone would exclude the free places.
        if levels and 0 not in wanted:
            body["priceLevels"] = levels

        async def fetch() -> list[Place]:
            data = await self._json(
                "POST", SEARCH_URL, json=body, headers=self._headers(SEARCH_FIELD_MASK)
            )
            found: list[Place] = []
            for raw in SearchResponse.model_validate(data).places:
                try:
                    found.append(place_from_google(GooglePlace.model_validate(raw)))
                except ValidationError as exc:
                    # One place with impossible coordinates is skipped; any other problem fails
                    # the whole call.
                    if not _only_location_errors(exc):
                        raise
                    logger.warning("%s: dropped a place with bad coordinates", self.provider)
            return found

        # Only the request is the memo key: the filters below run on every call.
        found = await self._memoized({"op": "search", "body": body}, fetch)
        # A place with no location came back inside the restriction rectangle; keep it.
        places = [p for p in found if p.location is None or near.distance_m(p.location) <= radius]
        if wanted:
            places = [p for p in places if p.price_level in wanted]
        if open_at is not None:
            places = [p for p in places if is_open_at(p.opening_periods, open_at) is not False]
        return places[:max_results]

    async def details(self, place_id: str) -> Place:
        async def fetch() -> Place:
            data = await self._json(
                "GET",
                DETAILS_URL + quote(place_id, safe=""),
                headers=self._headers(DETAILS_FIELD_MASK),
            )
            return place_from_google(GooglePlace.model_validate(data))

        return await self._memoized({"op": "details", "place_id": place_id}, fetch)
