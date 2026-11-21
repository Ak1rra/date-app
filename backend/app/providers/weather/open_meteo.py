"""Open-Meteo forecast API: free, no key. Hourly values for a date window, up to 16 days out."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.providers.cache import bucket_time
from app.providers.http import RealProvider, Runtime
from app.providers.types import Forecast, HourlyWeather, LatLng, forecast_from_json, to_jsonable
from app.providers.weather.base import ATTRIBUTION, assess, too_far
from app.providers.weather.schema import ForecastResponse

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HORIZON_DAYS = 16  # today plus 15 more days
HOURLY = "temperature_2m,precipitation_probability,precipitation,weather_code,wind_speed_10m"


def _naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None)  # wall-clock reading, see app.providers.types


def parse_hourly(data: Any) -> list[HourlyWeather]:
    """The forecast hours in an Open-Meteo body. Raises ValueError (a ValidationError) when the
    body is not a forecast: an empty `time` list is an empty forecast, a missing `hourly` is not.
    An hour with no temperature or weather code is left out."""
    hourly = ForecastResponse.model_validate(data).hourly
    n = len(hourly.time)
    probs = hourly.precipitation_probability or [None] * n
    winds = hourly.wind_speed_10m or [None] * n
    rows = []
    for moment, temp, prob, code, wind in zip(
        hourly.time, hourly.temperature_2m, probs, hourly.weather_code, winds, strict=True
    ):
        if temp is None or code is None:
            continue
        rows.append(
            HourlyWeather(
                time=datetime.fromisoformat(moment),
                temp_c=temp,
                precip_prob=None if prob is None else int(prob),
                weather_code=code,
                wind_kmh=wind,
            )
        )
    return rows


class OpenMeteoWeather(RealProvider):
    provider = "open_meteo"

    def __init__(
        self,
        *,
        ttl: int = 3600,
        client: httpx.AsyncClient | None = None,
        runtime: Runtime | None = None,
        now: datetime | None = None,
    ) -> None:
        super().__init__(client=client, runtime=runtime)
        self._ttl = ttl
        self._now = now  # a fixed "now", for tests

    async def forecast(self, location: LatLng, start: datetime, end: datetime) -> Forecast:
        # The window to the hour, which is also the cache bucket: forecasts change hourly.
        first = bucket_time(_naive(start), 60)
        last = max(bucket_time(_naive(end), 60), first)
        today = (self._now or datetime.now(UTC)).date()
        horizon = today + timedelta(days=HORIZON_DAYS - 1)
        if first.date() > horizon:
            return too_far()
        params = {
            "latitude": round(location.lat, 3),
            "longitude": round(location.lng, 3),
            "hourly": HOURLY,
            "timezone": "auto",
            "start_date": first.date().isoformat(),
            "end_date": min(last.date(), horizon).isoformat(),
        }

        async def fetch() -> Forecast:
            data = await self._json("GET", FORECAST_URL, params=params)
            hours = [h for h in parse_hourly(data) if first <= h.time <= last]
            summary, risk = assess(hours)
            return Forecast(hourly=hours, summary=summary, risk=risk, attribution=ATTRIBUTION)

        return await self._cached(
            {"op": "forecast", "params": params, "start": first, "end": last},
            self._ttl,
            fetch,
            encode=to_jsonable,
            decode=forecast_from_json,
        )
