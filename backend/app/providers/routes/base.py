from datetime import datetime
from typing import Protocol

from app.providers.types import LatLng, TravelLegEstimate

MODES = ("drive", "transit", "walk", "bicycle", "rideshare")  # rideshare is priced as drive


class RoutesProvider(Protocol):
    async def travel(
        self, origin: LatLng, dest: LatLng, mode: str, depart_at: datetime
    ) -> TravelLegEstimate:
        """Travel time and distance for one leg. `mode` is one of MODES (rideshare = drive).
        Raises ProviderUnavailable (reason "not_found" when no route exists)."""
        ...
