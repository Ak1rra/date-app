"""Calendar request and response models (FR-CAL-*)."""

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field

CalendarKind = Literal["date", "occasion"]
BestTime = Literal["day", "evening", "weekend", "any"]


class CalendarEntry(BaseModel):
    kind: CalendarKind
    id: uuid.UUID
    title: str
    starts_at: dt.datetime
    ends_at: dt.datetime | None
    all_day: bool
    # The remaining fields are set for kind = "date" only.
    itinerary_id: uuid.UUID | None = None
    status: str | None = None
    is_surprise: bool | None = None
    announced: bool | None = None


class SuggestTimesIn(BaseModel):
    duration_minutes: Annotated[int, Field(ge=30, le=720)]
    weeks: Annotated[int, Field(ge=1, le=6)] = 3
    idea_id: uuid.UUID | None = None
    best_time: BestTime | None = None


class TimeSlot(BaseModel):
    starts_at: dt.datetime
    ends_at: dt.datetime
    reason: str


class SuggestTimesOut(BaseModel):
    slots: list[TimeSlot]
