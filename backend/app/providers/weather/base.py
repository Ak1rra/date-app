from datetime import datetime
from typing import Literal, Protocol

from app.providers.types import Forecast, HourlyWeather, LatLng

ATTRIBUTION = "Weather data by Open-Meteo.com"
TOO_FAR = "Too far out for a forecast"

# Risk rule (design: "meaningful" ~ precipitation probability >= 40 % or a thunder, snow or
# heavy-rain code during the window).
MEANINGFUL_PROB = 40
LOW_PROB = 20
THUNDER = {95, 96, 97, 99}
SNOW = {71, 73, 75, 77, 85, 86}
HEAVY_RAIN = {65, 66, 67, 82}
MEANINGFUL_CODES = THUNDER | SNOW | HEAVY_RAIN
LOW_CODES = {45, 48, 51, 53, 55, 56, 57, 61, 63, 80, 81}  # fog, drizzle, light/moderate rain

_LABELS = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers",
    81: "Showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorms",
    96: "Thunderstorms with hail",
    97: "Thunderstorms",
    99: "Thunderstorms with hail",
}


def condition_label(code: int) -> str | None:
    """The plain-words label for a WMO weather code, or None if it has none."""
    return _LABELS.get(code)


class WeatherProvider(Protocol):
    async def forecast(self, location: LatLng, start: datetime, end: datetime) -> Forecast:
        """Hourly weather for the window (local wall-clock times at `location`). Beyond the
        forecast horizon: Forecast(hourly=[], summary="Too far out for a forecast",
        risk="none"). Raises ProviderUnavailable."""
        ...


def assess(hourly: list[HourlyWeather]) -> tuple[str, Literal["none", "low", "meaningful"]]:
    """A one-line summary and the risk level for the hours of a window."""
    if not hourly:
        return "No forecast available for that window", "none"
    probs = [h.precip_prob for h in hourly if h.precip_prob is not None]
    top_prob = max(probs, default=None)
    codes = {h.weather_code for h in hourly}
    if codes & MEANINGFUL_CODES or (top_prob is not None and top_prob >= MEANINGFUL_PROB):
        risk: Literal["none", "low", "meaningful"] = "meaningful"
    elif codes & LOW_CODES or (top_prob is not None and top_prob >= LOW_PROB):
        risk = "low"
    else:
        risk = "none"
    worst = max(codes, key=lambda c: (c in MEANINGFUL_CODES, c in LOW_CODES, c))
    temps = [h.temp_c for h in hourly]
    summary = f"{_LABELS.get(worst, 'Mixed conditions')}, {min(temps):.0f} to {max(temps):.0f} C"
    if top_prob is not None:
        summary += f", up to {top_prob}% chance of precipitation"
    return summary, risk


def too_far() -> Forecast:
    return Forecast(hourly=[], summary=TOO_FAR, risk="none", attribution=ATTRIBUTION)
