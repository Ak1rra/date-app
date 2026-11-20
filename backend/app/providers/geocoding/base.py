from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GeocodeResult:
    label: str
    lat: float
    lng: float
    place_id: str | None


class Geocoder(Protocol):
    async def geocode(self, query: str) -> GeocodeResult: ...
