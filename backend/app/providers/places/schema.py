"""Response models for the Google Places API (New): one place, as Text Search and Place Details
return it (the fields in the field masks), and the Text Search envelope."""

from typing import Any

from pydantic import Field, FiniteFloat

from app.providers.schema import Latitude, Longitude, ResponseModel


class DisplayName(ResponseModel):
    text: str


class Location(ResponseModel):
    latitude: Latitude
    longitude: Longitude


class Point(ResponseModel):
    """A day (Sunday = 0) and time. Google leaves out a zero field."""

    day: int = Field(0, ge=0, le=6)
    hour: int = Field(0, ge=0, le=23)
    minute: int = Field(0, ge=0, le=59)


class Period(ResponseModel):
    open: Point
    close: Point | None = None


class OpeningHours(ResponseModel):
    periods: list[Period] | None = None


class GooglePlace(ResponseModel):
    id: str = Field(min_length=1)
    displayName: DisplayName | None = None
    formattedAddress: str | None = None
    location: Location | None = None
    rating: FiniteFloat | None = None
    userRatingCount: int | None = Field(None, ge=0)
    priceLevel: str | None = None
    types: list[str] = Field(default_factory=list)
    websiteUri: str | None = None
    googleMapsUri: str | None = None
    regularOpeningHours: OpeningHours | None = None
    accessibilityOptions: dict[str, bool] | None = None


class SearchResponse(ResponseModel):
    """Text Search leaves `places` out when nothing matches. Each entry is validated on its own
    as a GooglePlace, so that one place with impossible coordinates can be skipped."""

    places: list[Any] = Field(default_factory=list)
