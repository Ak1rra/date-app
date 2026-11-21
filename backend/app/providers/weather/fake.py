"""Deterministic weather for DEMO_MODE and tests: sunny, except rain on dates whose day of the
month is divisible by 5. No horizon: any date gets a forecast."""

from datetime import datetime, timedelta

from app.providers.types import Forecast, HourlyWeather, LatLng
from app.providers.weather.base import ATTRIBUTION, assess

SUNNY_CODE = 0
RAIN_CODE = 63
MAX_HOURS = 24 * 16


def _hour_weather(moment: datetime) -> HourlyWeather:
    rainy = moment.day % 5 == 0
    # A mild day: coolest overnight, warmest at 15:00.
    temp = 12.0 + 10.0 * max(0.0, 1 - abs(moment.hour - 15) / 12)
    return HourlyWeather(
        time=moment,
        temp_c=round(temp - (4.0 if rainy else 0.0), 1),
        precip_prob=80 if rainy else 5,
        weather_code=RAIN_CODE if rainy else SUNNY_CODE,
        wind_kmh=18.0 if rainy else 8.0,
    )


class FakeWeather:
    async def forecast(self, location: LatLng, start: datetime, end: datetime) -> Forecast:
        first = start.replace(minute=0, second=0, microsecond=0, tzinfo=None)
        last = max(end.replace(tzinfo=None), first)
        hours: list[HourlyWeather] = []
        moment = first
        while moment <= last and len(hours) < MAX_HOURS:
            hours.append(_hour_weather(moment))
            moment += timedelta(hours=1)
        summary, risk = assess(hours)
        return Forecast(hourly=hours, summary=summary, risk=risk, attribution=ATTRIBUTION)
