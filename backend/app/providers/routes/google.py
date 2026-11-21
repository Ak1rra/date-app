"""Google Routes API: computeRoutes with the smallest field mask (duration and distance).

Google's terms forbid storing its responses: results are only memoized for the length of one run
(see RunMemo).
"""

import math
from datetime import UTC, datetime
from typing import Any

import httpx

from app.providers.cache import bucket_time
from app.providers.http import RealProvider, Runtime
from app.providers.routes.schema import RoutesResponse
from app.providers.types import (
    NOT_FOUND,
    LatLng,
    ProviderUnavailable,
    TravelLegEstimate,
)

COMPUTE_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = "routes.duration,routes.distanceMeters"
DEPART_BUCKET_MIN = 15

TRAVEL_MODES = {
    "drive": "DRIVE",
    "rideshare": "DRIVE",
    "transit": "TRANSIT",
    "walk": "WALK",
    "bicycle": "BICYCLE",
}


def _waypoint(point: LatLng) -> dict[str, Any]:
    return {"location": {"latLng": {"latitude": point.lat, "longitude": point.lng}}}


class GoogleRoutes(RealProvider):
    provider = "google_routes"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        runtime: Runtime | None = None,
        now: datetime | None = None,
    ) -> None:
        super().__init__(client=client, runtime=runtime)
        self._api_key = api_key
        self._now = now  # a fixed "now", for tests

    async def travel(
        self, origin: LatLng, dest: LatLng, mode: str, depart_at: datetime
    ) -> TravelLegEstimate:
        travel_mode = TRAVEL_MODES.get(mode)
        if travel_mode is None:
            raise ValueError(f"unknown travel mode {mode!r}")
        departure = bucket_time(
            depart_at if depart_at.tzinfo else depart_at.replace(tzinfo=UTC), DEPART_BUCKET_MIN
        )
        body: dict[str, Any] = {
            "origin": _waypoint(origin),
            "destination": _waypoint(dest),
            "travelMode": travel_mode,
            "units": "METRIC",
        }
        # Past departure times are only accepted for transit; traffic awareness is driving only.
        if travel_mode == "TRANSIT" or departure > (self._now or datetime.now(UTC)):
            body["departureTime"] = departure.strftime("%Y-%m-%dT%H:%M:%SZ")
        if travel_mode == "DRIVE":
            body["routingPreference"] = "TRAFFIC_AWARE"

        async def fetch() -> TravelLegEstimate:
            data = await self._json(
                "POST",
                COMPUTE_URL,
                json=body,
                headers={"X-Goog-Api-Key": self._api_key, "X-Goog-FieldMask": FIELD_MASK},
            )
            routes = RoutesResponse.model_validate(data).routes
            if not routes:
                raise ProviderUnavailable(self.provider, NOT_FOUND)
            route = routes[0]
            return TravelLegEstimate(
                mode=mode,
                duration_minutes=max(1, math.ceil(route.duration / 60)),
                distance_m=None if route.distanceMeters is None else int(route.distanceMeters),
            )

        # The requested mode is in the key: drive and rideshare send the same Google request.
        return await self._memoized({"op": "travel", "mode": mode, "body": body}, fetch)
