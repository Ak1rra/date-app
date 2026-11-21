"""Response models for the Ticketmaster Discovery API v2 events.json call."""

from datetime import date, datetime, time
from typing import Self

from pydantic import Field, FiniteFloat, model_validator

from app.providers.schema import Latitude, Longitude, ResponseModel


class Page(ResponseModel):
    totalElements: int = Field(ge=0)


class Start(ResponseModel):
    """An event with no `localDate` (date to be announced) cannot be placed in a plan."""

    localDate: date | None = None
    localTime: time | None = None
    dateTime: datetime | None = None


class Dates(ResponseModel):
    start: Start | None = None


class Price(ResponseModel):
    min: FiniteFloat | None = None
    max: FiniteFloat | None = None
    currency: str | None = None


class Named(ResponseModel):
    name: str | None = None


class Classification(ResponseModel):
    segment: Named | None = None
    genre: Named | None = None


class Address(ResponseModel):
    line1: str | None = None


class State(ResponseModel):
    stateCode: str | None = None


class VenueLocation(ResponseModel):
    """Ticketmaster sends the coordinates as strings."""

    latitude: Latitude
    longitude: Longitude


class Venue(ResponseModel):
    name: str | None = None
    address: Address | None = None
    city: Named | None = None
    state: State | None = None
    location: VenueLocation | None = None


class EventEmbedded(ResponseModel):
    venues: list[Venue] | None = None


class TicketmasterEvent(ResponseModel):
    id: str = Field(min_length=1)
    name: str | None = None
    url: str | None = None
    dates: Dates | None = None
    priceRanges: list[Price] | None = None
    classifications: list[Classification] | None = None
    embedded: EventEmbedded | None = Field(None, alias="_embedded")


class EventsEmbedded(ResponseModel):
    events: list[TicketmasterEvent]


class EventsResponse(ResponseModel):
    """`page` is always there. `_embedded` is left out only when nothing matched."""

    page: Page
    embedded: EventsEmbedded | None = Field(None, alias="_embedded")

    @model_validator(mode="after")
    def _embedded_unless_empty(self) -> Self:
        if self.embedded is None and self.page.totalElements != 0:
            raise ValueError("events are missing from a non-empty result")
        return self
