from datetime import datetime
from typing import Protocol

from app.providers.types import Event, LatLng


class EventsProvider(Protocol):
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
        """Events near a point between `start` and `end` (UTC if timezone-aware, else read as
        UTC), soonest first. Raises ProviderUnavailable."""
        ...
