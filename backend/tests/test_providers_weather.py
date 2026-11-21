"""OpenMeteoWeather (httpx.MockTransport, no network), the risk rule and FakeWeather."""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.providers.types import HourlyWeather, LatLng, ProviderUnavailable
from app.providers.weather.base import ATTRIBUTION, TOO_FAR, assess
from app.providers.weather.fake import FakeWeather
from app.providers.weather.open_meteo import FORECAST_URL, HOURLY, OpenMeteoWeather
from tests.provider_helpers import ok_json

PLACE = LatLng(47.6062, -122.3321)
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def hourly_json(day: str, probs: list[int | None], codes: list[int]) -> dict[str, Any]:
    hours = list(range(16, 16 + len(codes)))
    return {
        "hourly": {
            "time": [f"{day}T{h:02d}:00" for h in hours],
            "temperature_2m": [10.0 + i for i in range(len(codes))],
            "precipitation_probability": probs,
            "precipitation": [0.0] * len(codes),
            "weather_code": codes,
            "wind_speed_10m": [12.5] * len(codes),
        }
    }


def make(handler: Any, **kwargs: Any) -> OpenMeteoWeather:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenMeteoWeather(client=client, now=NOW, **kwargs)


def hour(code: int, prob: int | None, temp: float = 12.0) -> HourlyWeather:
    return HourlyWeather(datetime(2026, 11, 20, 18), temp, prob, code, 5.0)


async def test_forecast_requests_the_window_and_slices_the_hours() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=hourly_json("2026-11-21", [5, 10, 20, 30, 15], [0, 1, 2, 3, 3])
        )

    forecast = await make(handler).forecast(
        PLACE, datetime(2026, 11, 21, 17, 30), datetime(2026, 11, 21, 19, 15)
    )
    (request,) = seen
    assert str(request.url).startswith(FORECAST_URL)
    assert dict(request.url.params) == {
        "latitude": "47.606",
        "longitude": "-122.332",
        "hourly": HOURLY,
        "timezone": "auto",
        "start_date": "2026-11-21",
        "end_date": "2026-11-21",
    }
    # 17:30 floors to 17:00 and 19:15 to 19:00, so the hours 17, 18 and 19 are kept.
    assert [h.time.hour for h in forecast.hourly] == [17, 18, 19]
    first = forecast.hourly[0]
    assert (first.temp_c, first.precip_prob, first.weather_code, first.wind_kmh) == (
        11.0,
        10,
        1,
        12.5,
    )
    assert forecast.risk == "low"  # a 30 % hour in the window
    assert "30%" in forecast.summary
    assert forecast.attribution == ATTRIBUTION


async def test_forecast_tolerates_missing_probabilities() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=hourly_json("2026-11-21", [None, None], [0, 1]))

    forecast = await make(handler).forecast(
        PLACE, datetime(2026, 11, 21, 16), datetime(2026, 11, 21, 17)
    )
    assert [h.precip_prob for h in forecast.hourly] == [None, None]
    assert forecast.risk == "none"
    assert "chance of precipitation" not in forecast.summary


async def test_forecast_beyond_the_horizon_makes_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    forecast = await make(handler).forecast(
        PLACE, datetime(2026, 12, 10, 18), datetime(2026, 12, 10, 22)
    )
    assert forecast.hourly == []
    assert forecast.summary == TOO_FAR
    assert forecast.risk == "none"


async def test_forecast_clamps_a_window_that_runs_past_the_horizon() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"hourly": {"time": [], "temperature_2m": [], "weather_code": []}}
        )

    forecast = await make(handler).forecast(
        PLACE, datetime(2026, 12, 5, 18), datetime(2026, 12, 8, 22)
    )
    assert dict(seen[0].url.params)["end_date"] == "2026-12-05"  # today + 15 days
    assert forecast.hourly == [] and forecast.risk == "none"


@pytest.mark.parametrize(
    ("status", "reason"), [(429, "over_quota"), (500, "down"), (400, "down"), (403, "no_key")]
)
async def test_http_errors_become_provider_unavailable(status: int, reason: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": True, "reason": "nope"})

    with pytest.raises(ProviderUnavailable) as info:
        await make(handler).forecast(PLACE, datetime(2026, 11, 21, 18), datetime(2026, 11, 21, 20))
    assert (info.value.provider, info.value.reason) == ("open_meteo", reason)


def hourly_body(**columns: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "time": ["2026-11-21T18:00", "2026-11-21T19:00"],
        "temperature_2m": [10.0, 11.0],
        "weather_code": [1, 2],
    }
    return {"hourly": {**base, **columns}}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"error": "bad"},
        {"error": True, "reason": "nope"},
        ["not", "an", "object"],
        "text",
        {"hourly": {}},  # an empty object where `time` and the columns are required
        {"hourly": 5},
        {"hourly": None},
        hourly_body(time="2026-11-21T18:00"),  # a string where a list is expected
        hourly_body(time=None),
        hourly_body(time=[5, 6]),
        hourly_body(time=["not a time", "2026-11-21T19:00"]),
        hourly_body(temperature_2m="10"),
        hourly_body(temperature_2m=[10.0, float("nan")]),
        hourly_body(temperature_2m=["hot", 11.0]),
        hourly_body(temperature_2m=[{}, 11.0]),
        hourly_body(temperature_2m=[10.0]),  # a column shorter than `time`
        hourly_body(weather_code=[1, 2, 3]),  # and one that is longer
        hourly_body(weather_code=["x", 2]),
        hourly_body(precipitation_probability=[float("inf"), 10]),
        hourly_body(precipitation_probability="high"),
        hourly_body(wind_speed_10m=[float("nan"), 1.0]),
        hourly_body(wind_speed_10m=[1.0]),
    ],
)
async def test_malformed_ok_bodies_are_bad_responses(body: Any) -> None:
    with pytest.raises(ProviderUnavailable) as info:
        await make(lambda request: ok_json(body)).forecast(
            PLACE, datetime(2026, 11, 21, 18), datetime(2026, 11, 21, 20)
        )
    assert (info.value.provider, info.value.reason) == ("open_meteo", "bad_response")


async def test_non_finite_forecast_values_are_rejected() -> None:
    for column in ("temperature_2m", "wind_speed_10m"):
        body = hourly_body(**{column: [10.0, float("nan")]})
        with pytest.raises(ProviderUnavailable) as info:
            await make(lambda request, body=body: ok_json(body)).forecast(
                PLACE, datetime(2026, 11, 21, 18), datetime(2026, 11, 21, 20)
            )
        assert info.value.reason == "bad_response"


async def test_an_empty_time_list_is_still_a_valid_empty_forecast() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"hourly": {"time": [], "temperature_2m": [], "weather_code": []}}
        )

    forecast = await make(handler).forecast(
        PLACE, datetime(2026, 11, 21, 18), datetime(2026, 11, 21, 20)
    )
    assert forecast.hourly == [] and forecast.risk == "none"


async def test_timeout_becomes_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderUnavailable) as info:
        await make(handler).forecast(PLACE, datetime(2026, 11, 21, 18), datetime(2026, 11, 21, 20))
    assert info.value.reason == "down"


# --- the risk rule


def test_risk_rule() -> None:
    assert assess([])[1] == "none"
    assert assess([hour(0, 5), hour(1, 10)])[1] == "none"
    assert assess([hour(0, 25)])[1] == "low"
    assert assess([hour(61, None)])[1] == "low"  # light rain, no probability
    assert assess([hour(0, 40)])[1] == "meaningful"
    assert assess([hour(95, 10)])[1] == "meaningful"  # thunderstorm
    assert assess([hour(73, 10)])[1] == "meaningful"  # snow
    assert assess([hour(65, None)])[1] == "meaningful"  # heavy rain


def test_summary_names_the_worst_conditions_and_the_temperature_range() -> None:
    summary, risk = assess([hour(0, 5, 8.0), hour(95, 70, 14.0), hour(3, 10, 11.0)])
    assert summary.startswith("Thunderstorms, 8 to 14")
    assert "70%" in summary
    assert risk == "meaningful"


# --- FakeWeather


async def test_fake_weather_is_sunny_except_on_days_divisible_by_five() -> None:
    fake = FakeWeather()
    sunny = await fake.forecast(PLACE, datetime(2026, 11, 21, 18), datetime(2026, 11, 21, 22))
    assert sunny.risk == "none"
    assert [h.time.hour for h in sunny.hourly] == [18, 19, 20, 21, 22]
    assert all(h.weather_code == 0 and h.precip_prob == 5 for h in sunny.hourly)
    rainy = await fake.forecast(PLACE, datetime(2026, 11, 20, 18), datetime(2026, 11, 20, 22))
    assert rainy.risk == "meaningful"
    assert all(h.weather_code == 63 and h.precip_prob == 80 for h in rainy.hourly)
    assert rainy.attribution == ATTRIBUTION
    # The 5th and the 30th are rainy too; the 6th is not.
    for day, risk in ((5, "meaningful"), (30, "meaningful"), (6, "none")):
        window = (datetime(2026, 11, day, 12), datetime(2026, 11, day, 13))
        assert (await fake.forecast(PLACE, *window)).risk == risk


async def test_fake_weather_is_deterministic_and_spans_midnight() -> None:
    fake = FakeWeather()
    window = (datetime(2026, 11, 19, 22), datetime(2026, 11, 20, 2))
    a = await fake.forecast(PLACE, *window)
    assert a == await FakeWeather().forecast(PLACE, *window)
    assert len(a.hourly) == 5
    assert a.risk == "meaningful"  # the 20th is in the window
    assert (await fake.forecast(PLACE, window[0], window[0])).hourly[0].time == window[0]
