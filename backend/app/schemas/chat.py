"""AI chat request and response models (AI-CHAT), and the proposal tools' inputs."""

import datetime as dt
import uuid
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.schemas.ideas import Cost, Duration, IdeaIn, IdeaOut
from app.schemas.itineraries import ItineraryOut, LongText, Title

ChatScope = Literal["private", "shared"]
ChatRole = Literal["user", "assistant"]
ChatActionType = Literal["idea", "itinerary_change", "calendar_slot"]

# --- Proposal tool inputs (what the model may propose; nothing is saved until applied)


class ProposeIdea(IdeaIn):
    """The idea fields as IdeaIn, but unknown properties are rejected and the numbers must be
    numbers ("25" is not a cost)."""

    model_config = ConfigDict(extra="forbid")

    est_cost: Annotated[Cost, Field(strict=True)] | None = None
    duration_minutes: Annotated[Duration, Field(strict=True)] | None = None


class ProposeStop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    place_id: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    notes: LongText | None = None

    @model_validator(mode="after")
    def _ends_after_start(self) -> Self:
        if self.ends_at < self.starts_at:
            raise ValueError("ends_at must not be before starts_at")
        return self


class ProposeItineraryChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    itinerary_id: uuid.UUID
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ]
    stops: Annotated[list[ProposeStop], Field(min_length=1, max_length=8)] | None = None


class ProposeCalendarSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    itinerary_id: uuid.UUID | None = None
    idea_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _ends_after_start(self) -> Self:
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if self.itinerary_id is not None and self.idea_id is not None:
            raise ValueError("give itinerary_id or idea_id, not both")
        return self


# --- Requests


class ChatMessageIn(BaseModel):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


# --- Responses


class ChatThreadOut(BaseModel):
    id: uuid.UUID
    scope: ChatScope
    title: str | None
    updated_at: dt.datetime


class ChatActionOut(BaseModel):
    id: uuid.UUID
    type: ChatActionType
    summary: str
    payload: dict[str, Any]
    applied_at: dt.datetime | None
    result: dict[str, Any] | None


class ChatMessageOut(BaseModel):
    id: uuid.UUID
    role: ChatRole
    author_user_id: uuid.UUID | None
    author_is_me: bool
    content: str
    actions: list[ChatActionOut]
    created_at: dt.datetime


class ChatMessagesOut(BaseModel):
    """Oldest to newest. `has_more` says there are older messages than this page."""

    messages: list[ChatMessageOut]
    has_more: bool


class ChatActionApplyOut(BaseModel):
    """`idea` / `itinerary` are what this call created or changed; both are null when the
    action had already been applied."""

    action: ChatActionOut
    idea: IdeaOut | None
    itinerary: ItineraryOut | None
