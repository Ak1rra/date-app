from datetime import datetime
from typing import Protocol

from app.providers.types import LatLng, Place


class PlacesProvider(Protocol):
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
        """Places matching `query` near a point. `open_at` is a wall-clock time at the venue;
        places whose hours are unknown are kept. Raises ProviderUnavailable."""
        ...

    async def details(self, place_id: str) -> Place:
        """One place by id. Raises ProviderUnavailable (reason "not_found" for an unknown id)."""
        ...
