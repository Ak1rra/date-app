"""Deterministic travel estimates for DEMO_MODE and tests: straight-line distance over the mode's
typical speed, plus 3 minutes (parking, waiting, locking up)."""

from datetime import datetime

from app.providers.types import LatLng, TravelLegEstimate

SPEED_KMH = {"walk": 4.5, "bicycle": 14.0, "transit": 20.0, "drive": 30.0, "rideshare": 30.0}
OVERHEAD_MIN = 3


class FakeRoutes:
    async def travel(
        self, origin: LatLng, dest: LatLng, mode: str, depart_at: datetime
    ) -> TravelLegEstimate:
        speed = SPEED_KMH.get(mode)
        if speed is None:
            raise ValueError(f"unknown travel mode {mode!r}")
        meters = origin.distance_m(dest)
        minutes = meters / 1000 / speed * 60 + OVERHEAD_MIN
        return TravelLegEstimate(
            mode=mode, duration_minutes=round(minutes), distance_m=round(meters)
        )
