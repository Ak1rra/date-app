"""Structured outputs of the planning runs (design: m2-ai-plan.md, "Pipelines").

Every model forbids extra fields and bounds every string, list and number, so the schema the
model is held to is strict. Venue facts the model may not make up (addresses, ratings, booking
links) are not fields here at all: the server fills them from provider data by id.
"""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

StopKind = Literal["food", "drink", "activity", "event", "outdoors", "other"]
TransportMode = Literal["drive", "walk", "transit", "bicycle", "rideshare"]  # = travel_legs.mode
ChecklistKind = Literal["reserve", "buy", "bring", "arrange", "wear", "other"]

Cost = Annotated[float, Field(ge=0, le=100_000)]
Id = Annotated[str, Field(min_length=1, max_length=200)]  # "" would slip past truthiness checks


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanOptionStop(_Strict):
    title: Annotated[str, Field(max_length=120)]
    kind: StopKind
    place_id: Id | None = None
    event_id: Id | None = None
    evergreen: bool
    est_cost: Cost | None = None


class PlanOption(_Strict):
    title: Annotated[str, Field(max_length=120)]
    pitch: Annotated[str, Field(max_length=400)]
    why_for_them: Annotated[str, Field(max_length=300)]
    stops: Annotated[list[PlanOptionStop], Field(min_length=1, max_length=5)]
    est_cost: Cost | None = None
    grounded: bool
    occasion: Annotated[str, Field(max_length=80)] | None = None


class PlanOptions(_Strict):
    options: Annotated[list[PlanOption], Field(min_length=3, max_length=3)]


class DraftStop(_Strict):
    title: Annotated[str, Field(max_length=120)]
    place_id: Id | None = None
    event_id: Id | None = None
    evergreen: bool
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    description: Annotated[str, Field(max_length=600)]
    est_cost: Cost | None = None
    notes: Annotated[str, Field(max_length=300)] | None = None
    # Ignored by the server: booking links come from provider data only.
    booking_url: Annotated[str, Field(max_length=500)] | None = None


class DraftChecklistItem(_Strict):
    text: Annotated[str, Field(max_length=300)]
    kind: ChecklistKind
    due_at: AwareDatetime | None = None
    link: Annotated[str, Field(max_length=500)] | None = None


class ItineraryDraft(_Strict):
    title: Annotated[str, Field(max_length=200)]
    stops: Annotated[list[DraftStop], Field(min_length=1, max_length=8)]  # = StopsPut's cap
    transport_mode: TransportMode
    backup_plan: Annotated[str, Field(max_length=600)] | None = None
    bring_wear: Annotated[str, Field(max_length=300)] | None = None
    checklist: Annotated[list[DraftChecklistItem], Field(max_length=8)] = Field(
        default_factory=list
    )
    invitation_message: Annotated[str, Field(max_length=600)] | None = None


class PolishedInvitation(_Strict):
    text: Annotated[str, Field(min_length=1, max_length=1000)]


@dataclass(frozen=True)
class Issue:
    """A problem `validate_itinerary` found. Not model output. `stop_index` is None for an
    issue about the whole itinerary (cost)."""

    stop_index: int | None
    code: str
    message: str
