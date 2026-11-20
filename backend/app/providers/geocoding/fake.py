import hashlib

from app.providers.geocoding.base import GeocodeResult

# Demo centre: Austin, TX.
DEMO_LAT = 30.2672
DEMO_LNG = -97.7431
MAX_OFFSET = 0.05


def _offset(digest: bytes) -> float:
    """Map 4 digest bytes to [-MAX_OFFSET, MAX_OFFSET]."""
    return (int.from_bytes(digest, "big") / 0xFFFFFFFF * 2 - 1) * MAX_OFFSET


class FakeGeocoder:
    """Deterministic geocoder for DEMO_MODE and tests."""

    async def geocode(self, query: str) -> GeocodeResult:
        normalized = " ".join(query.split())
        digest = hashlib.sha256(normalized.lower().encode()).digest()
        return GeocodeResult(
            label=f"{normalized.title()} (demo)",
            lat=round(DEMO_LAT + _offset(digest[:4]), 6),
            lng=round(DEMO_LNG + _offset(digest[4:8]), 6),
            place_id=f"demo:{digest[:6].hex()}",
        )
