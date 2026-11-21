import hashlib

from app.providers.geocoding.base import GeocodeNotFound, GeocodeResult

# Demo centre: Austin, TX.
DEMO_LAT = 30.2672
DEMO_LNG = -97.7431
MAX_OFFSET = 0.05


def _offset(digest: bytes) -> float:
    """Map 4 digest bytes to [-MAX_OFFSET, MAX_OFFSET]."""
    return (int.from_bytes(digest, "big") / 0xFFFFFFFF * 2 - 1) * MAX_OFFSET


class FakeGeocoder:
    """Deterministic geocoder for DEMO_MODE and tests. Every non-blank query is found; a blank
    one raises GeocodeNotFound, as the real geocoder does for a query with no match. The label
    never holds any of the query (NFR-PRIV-1: it is stored as the home area): it is "Demo area
    n", with n from 1 to 99 taken from the same hash as the coordinates."""

    async def geocode(self, query: str) -> GeocodeResult:
        normalized = " ".join(query.split())
        if not normalized:
            raise GeocodeNotFound
        digest = hashlib.sha256(normalized.lower().encode()).digest()
        return GeocodeResult(
            label=f"Demo area {1 + int.from_bytes(digest[8:12], 'big') % 99}",
            lat=round(DEMO_LAT + _offset(digest[:4]), 6),
            lng=round(DEMO_LNG + _offset(digest[4:8]), 6),
            place_id=f"demo:{digest[:6].hex()}",
        )
