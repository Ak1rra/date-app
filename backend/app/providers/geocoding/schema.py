"""Response models for the Google Geocoding API. `status` decides what the rest of the body
must look like, so it is parsed first (Envelope), and `results` only when the status is OK."""

from pydantic import Field

from app.providers.schema import Latitude, Longitude, ResponseModel


class Envelope(ResponseModel):
    status: str


class Component(ResponseModel):
    long_name: str
    short_name: str | None = None
    types: list[str] = Field(default_factory=list)


class LatLngModel(ResponseModel):
    lat: Latitude
    lng: Longitude


class Geometry(ResponseModel):
    location: LatLngModel


class Result(ResponseModel):
    address_components: list[Component] = Field(default_factory=list)
    geometry: Geometry
    place_id: str | None = None


class OkResponse(ResponseModel):
    results: list[Result] = Field(min_length=1)
