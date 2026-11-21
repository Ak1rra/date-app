"""Itinerary request and response models (M3)."""

import datetime as dt
import uuid
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

ItineraryStatus = Literal["draft", "scheduled", "completed", "cancelled"]
RevealPolicy = Literal["with_itinerary", "at_stop_time"]
ChecklistKind = Literal["reserve", "buy", "bring", "arrange", "wear", "other"]
TravelMode = Literal["drive", "transit", "walk", "bicycle", "rideshare"]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Money = Annotated[float, Field(ge=0, le=99_999_999)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


def _http_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("must be an http or https link")
    return value


HttpLink = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=2000), AfterValidator(_http_url)
]


# --- Requests


class ItineraryPatch(BaseModel):
    """Leaving a field out means "unchanged"; an explicit null clears the optional ones."""

    title: Title = None  # type: ignore[assignment]
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    budget: Money | None = None
    backup_plan: LongText | None = None
    bring_wear: LongText | None = None
    invitation_message: LongText | None = None

    @model_validator(mode="after")
    def _title_not_null(self) -> "ItineraryPatch":
        # A title is required, so "title": null is a 422 rather than a clear.
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title can't be null")
        return self


class StopIn(BaseModel):
    id: uuid.UUID | None = None
    title: Title
    place_id: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    description: LongText | None = None
    est_cost: Money | None = None
    notes: LongText | None = None
    reveal_policy: RevealPolicy = "with_itinerary"

    @model_validator(mode="after")
    def _ends_after_start(self) -> "StopIn":
        if self.ends_at < self.starts_at:
            raise ValueError("ends_at must not be before starts_at")
        return self


class StopsPut(BaseModel):
    stops: Annotated[list[StopIn], Field(min_length=1, max_length=8)]


class ChecklistIn(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    kind: ChecklistKind
    due_at: AwareDatetime | None = None
    link: HttpLink | None = None


class ChecklistPatch(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)] = (
        None  # type: ignore[assignment]
    )
    kind: ChecklistKind = None  # type: ignore[assignment]
    due_at: AwareDatetime | None = None
    link: HttpLink | None = None
    done: bool = None  # type: ignore[assignment]


class ScheduleIn(BaseModel):
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    mode: Literal["announce", "surprise"]
    invitation_message: LongText | None = None


class ReactIn(BaseModel):
    kind: Literal["heart", "move"]
    suggested_start: AwareDatetime | None = None
    note: Note | None = None


# --- Responses


class VenueOut(BaseModel):
    """What we know about the place. Only lat/lng are ever stored; the rest is fetched live."""

    name: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    rating: float | None = None
    price_level: int | None = None
    website: str | None = None
    maps_uri: str | None = None


class TravelOut(BaseModel):
    mode: TravelMode
    duration_minutes: int
    distance_m: int | None
    source: Literal["live", "estimate"]


class MapsOut(BaseModel):
    apple: str | None = None
    google: str | None = None
    directions_from_previous: str | None = None


class StopOut(BaseModel):
    id: uuid.UUID
    position: int
    title: str
    starts_at: dt.datetime | None
    ends_at: dt.datetime | None
    description: str | None
    est_cost: float | None
    notes: str | None
    place_id: str | None
    event: dict[str, Any] | None
    reveal_policy: RevealPolicy
    warnings: list[dict[str, Any]]
    booking_url: str | None
    venue: VenueOut | None
    travel_to_next: TravelOut | None
    maps: MapsOut


class ChecklistItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    text: str
    kind: ChecklistKind
    due_at: dt.datetime | None
    link: str | None
    done: bool
    done_at: dt.datetime | None


class PartnerReaction(BaseModel):
    kind: Literal["heart", "move"]
    suggested_start: dt.datetime | None = None
    note: str | None = None
    at: dt.datetime


class ItineraryOut(BaseModel):
    id: uuid.UUID
    idea_id: uuid.UUID | None
    planner_user_id: uuid.UUID
    is_planner: bool
    title: str
    starts_at: dt.datetime | None
    ends_at: dt.datetime | None
    status: ItineraryStatus
    announced_at: dt.datetime | None
    invitation_message: str | None
    is_surprise: bool
    reveal_at: dt.datetime | None
    revealed_at: dt.datetime | None
    budget: float | None
    est_total: float | None
    currency: str
    weather: dict[str, Any] | None
    backup_plan: str | None
    bring_wear: str | None
    warnings: list[dict[str, Any]]
    partner_reaction: PartnerReaction | None
    seen_by_partner_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime
    stops: list[StopOut]
    checklist: list[ChecklistItemOut]
    attributions: list[str]


class ItinerarySummary(BaseModel):
    id: uuid.UUID
    title: str
    starts_at: dt.datetime | None
    ends_at: dt.datetime | None
    status: ItineraryStatus
    is_surprise: bool
    announced: bool
    is_planner: bool
    stop_count: int


class PlaceOut(BaseModel):
    """A venue search hit. Live provider data: never stored."""

    place_id: str
    name: str
    address: str | None
    rating: float | None
    price_level: int | None
    lat: float | None
    lng: float | None
    open_now: bool | None
    attributions: list[str]
