"""Google Geocoding API behind the Geocoder protocol.

Failures are ProviderUnavailable and a query with no match is GeocodeNotFound; the request path
that sets the couple's home area turns both into API errors. Google's terms forbid storing its
responses, so results are only memoized for the length of one run (see RunMemo).
"""

import httpx

from app.providers.geocoding.base import GeocodeNotFound, GeocodeResult
from app.providers.geocoding.schema import Component, Envelope, OkResponse
from app.providers.http import RealProvider, Runtime
from app.providers.types import DOWN, NO_KEY, OVER_QUOTA, ProviderUnavailable

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
NEIGHBORHOOD_TYPES = (
    "neighborhood",
    "sublocality",
    "sublocality_level_1",
    "sublocality_level_2",
    "sublocality_level_3",
    "sublocality_level_4",
    "sublocality_level_5",
)

_STATUS_REASONS = {
    "OVER_QUERY_LIMIT": OVER_QUOTA,
    "OVER_DAILY_LIMIT": OVER_QUOTA,
    "REQUEST_DENIED": NO_KEY,
    "INVALID_REQUEST": DOWN,
    "UNKNOWN_ERROR": DOWN,
}


def _component(components: list[Component], *types: str, short: bool = False) -> str | None:
    """The first address component carrying one of `types`, in the order of `types`."""
    for wanted in types:
        for component in components:
            if wanted in component.types:
                return (component.short_name if short else None) or component.long_name
    return None


def label_for(components: list[Component]) -> str:
    """A coarse label (NFR-PRIV-1), never a street address: "Hyde Park, Austin", else
    "Austin, TX", else "Texas, United States", else "Your area". Only these components are read,
    and the formatted address never is, because it holds the street."""
    hood = _component(components, *NEIGHBORHOOD_TYPES)
    city = _component(components, "locality", "postal_town", "administrative_area_level_3")
    state = _component(components, "administrative_area_level_1", short=True)
    region = _component(components, "administrative_area_level_1")
    country = _component(components, "country")
    if hood and city:
        return f"{hood}, {city}"
    if city and state:
        return f"{city}, {state}"
    if region and country:
        return f"{region}, {country}"
    return "Your area"


class GoogleGeocoder(RealProvider):
    provider = "google_geocode"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        runtime: Runtime | None = None,
    ) -> None:
        super().__init__(client=client, runtime=runtime)
        self._api_key = api_key

    async def geocode(self, query: str) -> GeocodeResult:
        normalized = " ".join(query.split())

        async def fetch() -> GeocodeResult:
            data = await self._json(
                "GET", GEOCODE_URL, params={"address": normalized, "key": self._api_key}
            )
            status = Envelope.model_validate(data).status
            if status == "ZERO_RESULTS":
                raise GeocodeNotFound
            if status != "OK":
                raise ProviderUnavailable(self.provider, _STATUS_REASONS.get(status, DOWN))
            result = OkResponse.model_validate(data).results[0]
            return GeocodeResult(
                label=label_for(result.address_components),
                lat=result.geometry.location.lat,
                lng=result.geometry.location.lng,
                place_id=result.place_id,
            )

        return await self._memoized({"op": "geocode", "query": normalized.lower()}, fetch)
