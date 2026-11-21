"""Response models for the Open-Meteo forecast API (the hourly columns we ask for)."""

from typing import Self

from pydantic import FiniteFloat, model_validator

from app.providers.schema import ResponseModel


class Hourly(ResponseModel):
    """Parallel columns, one value per entry of `time`. A value can be null (not forecast)."""

    time: list[str]
    temperature_2m: list[FiniteFloat | None]
    weather_code: list[int | None]
    precipitation_probability: list[FiniteFloat | None] | None = None
    wind_speed_10m: list[FiniteFloat | None] | None = None

    @model_validator(mode="after")
    def _same_length(self) -> Self:
        columns = (
            self.temperature_2m,
            self.weather_code,
            self.precipitation_probability,
            self.wind_speed_10m,
        )
        if any(column is not None and len(column) != len(self.time) for column in columns):
            raise ValueError("an hourly column is not as long as `time`")
        return self


class ForecastResponse(ResponseModel):
    hourly: Hourly
