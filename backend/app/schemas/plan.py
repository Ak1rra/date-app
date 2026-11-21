"""Plan-mode request and response models (m2-ai-plan.md, "API")."""

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints

from app.ai.schemas import PlanOption
from app.schemas.ideas import Cost

PlanStatus = Literal["open", "done", "archived"]

Steer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
MustInclude = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
CombineNote = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class PlanSessionIn(BaseModel):
    recipient_profile_id: uuid.UUID | None = None
    window_start: AwareDatetime
    window_end: AwareDatetime
    budget_max: Cost | None = None
    steer: Steer | None = None
    must_include: MustInclude | None = None
    surprise: bool = False


class PlanOptionsIn(BaseModel):
    more: bool = False


class PlanBuildIn(BaseModel):
    option_ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=3)]
    combine_note: CombineNote | None = None


class PlanOptionOut(PlanOption):
    id: uuid.UUID


class PlanSessionOut(BaseModel):
    id: uuid.UUID
    inputs: dict[str, Any]
    options: list[PlanOptionOut]
    chosen: dict[str, Any] | None
    itinerary_id: uuid.UUID | None
    status: PlanStatus
    created_at: dt.datetime


class PlanOptionsOut(PlanSessionOut):
    degraded: list[str]
