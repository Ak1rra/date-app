from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GeocodeResult:
    label: str
    lat: float
    lng: float
    place_id: str | None


class GeocodeNotFound(Exception):
    """The geocoder is up but found nothing for the query."""


class Geocoder(Protocol):
    async def geocode(self, query: str) -> GeocodeResult:
        """Raises GeocodeNotFound when nothing matches the query, and ProviderUnavailable when
        the geocoder cannot answer."""
        ...
