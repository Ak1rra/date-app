"""Date idea request and response models (FR-IDEA-*, FR-VOTE-*)."""

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

IdeaVisibility = Literal["private", "shared"]
IdeaStatus = Literal["active", "archived", "expired"]
IdeaSource = Literal["ai", "chat", "manual", "plan", "mentioned"]
BestTime = Literal["day", "evening", "weekend", "any"]
IndoorOutdoor = Literal["indoor", "outdoor", "either"]
VoteValue = Literal["yes", "no", "maybe"]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Pitch = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
Tags = Annotated[list[Tag], Field(max_length=10)]
Category = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
Cost = Annotated[float, Field(ge=0, le=99_999_999.99)]
Duration = Annotated[int, Field(ge=15, le=1440)]


class IdeaIn(BaseModel):
    title: Title
    pitch: Pitch = ""
    tags: Tags = []
    category: Category | None = None
    est_cost: Cost | None = None
    duration_minutes: Duration | None = None
    best_time: BestTime | None = None
    indoor_outdoor: IndoorOutdoor | None = None


class IdeaPatch(BaseModel):
    # Leaving a field out means "unchanged" (the service reads `model_fields_set`). The three
    # required fields cannot be null, so an explicit null is a 422 and the schema does not
    # advertise it (defaults are not validated); the optional ones can be cleared with null.
    title: Title = None  # type: ignore[assignment]
    pitch: Pitch = None  # type: ignore[assignment]
    tags: Tags = None  # type: ignore[assignment]
    category: Category | None = None
    est_cost: Cost | None = None
    duration_minutes: Duration | None = None
    best_time: BestTime | None = None
    indoor_outdoor: IndoorOutdoor | None = None


class VoteIn(BaseModel):
    value: VoteValue


class VenueOut(BaseModel):
    name: str | None
    rating: float | None
    price_level: int | None
    address: str | None
    distance_km: float | None


class EventOut(BaseModel):
    name: str
    local_date: str | None
    local_time: str | None
    url: str | None


class VotesOut(BaseModel):
    """`partner` stays null until both members have voted (FR-VOTE-2)."""

    mine: VoteValue | None
    partner: VoteValue | None


class IdeaOut(BaseModel):
    id: uuid.UUID
    title: str
    pitch: str
    tags: list[str]
    category: str | None
    est_cost: float | None
    currency: str
    duration_minutes: int | None
    best_time: BestTime | None
    indoor_outdoor: IndoorOutdoor | None
    grounded: bool
    venue: VenueOut | None
    event: EventOut | None
    valid_until: dt.datetime | None
    status: IdeaStatus
    may_have_changed: bool
    visibility: IdeaVisibility
    source: IdeaSource
    created_by_me: bool
    votes: VotesOut
    attributions: list[str]


class DeckOut(BaseModel):
    cards: list[IdeaOut]
    partner_joined: bool


class IdeaListsOut(BaseModel):
    matches: list[IdeaOut]
    maybes: list[IdeaOut]
    pending_partner: list[IdeaOut]
    passed: list[IdeaOut]
