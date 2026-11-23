"""Date idea request and response models (FR-IDEA-*, FR-VOTE-*)."""

import datetime as dt
import uuid
from typing import Annotated, Literal, Self

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    UrlConstraints,
    field_validator,
    model_validator,
)

from app.ai.schemas import StopKind

IdeaVisibility = Literal["private", "shared"]
IdeaStatus = Literal["active", "archived", "expired"]
IdeaSource = Literal["ai", "chat", "manual", "plan", "mentioned"]
BestTime = Literal["day", "evening", "weekend", "any"]
IndoorOutdoor = Literal["indoor", "outdoor", "either"]
Where = Literal["indoor", "outdoor", "at_home"]
VoteValue = Literal["yes", "no", "maybe"]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Pitch = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
Tags = Annotated[list[Tag], Field(max_length=10)]
Category = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
Cost = Annotated[float, Field(ge=0, le=99_999_999.99)]
Duration = Annotated[int, Field(ge=15, le=1440)]
VenueName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]
Link = Annotated[AnyHttpUrl, UrlConstraints(max_length=500, allowed_schemes=["http", "https"])]


class IdeaIn(BaseModel):
    title: Title
    pitch: Pitch = ""
    tags: Tags = []
    category: Category | None = None
    est_cost: Cost | None = None
    duration_minutes: Duration | None = None
    best_time: BestTime | None = None
    indoor_outdoor: IndoorOutdoor | None = None
    venue_name: VenueName | None = None
    link: Link | None = None


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
    venue_name: VenueName | None = None
    link: Link | None = None


class VoteIn(BaseModel):
    value: VoteValue


class VenueOut(BaseModel):
    name: str | None
    rating: float | None
    price_level: int | None
    address: str | None
    distance_km: float | None
    url: str | None = None  # a manual idea's own link; never a provider attribution


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


class GenerateIdeasIn(BaseModel):
    """What to dream up. `match` saves shared ideas for the deck, `plan` private ones."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["match", "plan"]
    count: Annotated[int, Field(ge=1, le=20)] = 8
    window_start: dt.date | None = None
    window_end: dt.date | None = None
    budget_max: Annotated[float, Field(gt=0, le=10_000)] | None = None
    vibe: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    # Indoor and outdoor are dates out of the house; at_home is one at home. Without at_home the
    # couple wants to leave the house.
    where: Annotated[list[Where], Field(min_length=1, max_length=3)] = ["indoor", "outdoor"]
    category: StopKind | None = None
    surprise_me: bool = False

    @field_validator("where")
    @classmethod
    def _where_unique(cls, value: list[Where]) -> list[Where]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _window_order(self) -> Self:
        if self.window_start and self.window_end and self.window_end < self.window_start:
            raise ValueError("window_end must not be before window_start")
        return self


class GenerateIdeasOut(BaseModel):
    ideas: list[IdeaOut]
    degraded: list[str]
