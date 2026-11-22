"""Feedback request and response models (M7): ratings, the planner's read, past dates."""

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Thumb = Literal["up", "down"]

Comment = Annotated[str, StringConstraints(strip_whitespace=True, max_length=280)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class RatingIn(BaseModel):
    stars: int = Field(ge=1, le=5)
    would_again: bool
    comment: Comment | None = None
    stop_thumbs: dict[uuid.UUID, Thumb] = Field(default_factory=dict)

    @field_validator("comment")
    @classmethod
    def _empty_comment_is_none(cls, value: str | None) -> str | None:
        return value or None


class RatingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stars: int
    would_again: bool
    comment: str | None
    stop_thumbs: dict[uuid.UUID, Thumb]
    updated_at: dt.datetime


class PlannerReadIn(BaseModel):
    enjoyment: int = Field(ge=1, le=5)
    highlights: Note | None = None
    misses: Note | None = None

    @field_validator("highlights", "misses")
    @classmethod
    def _empty_is_none(cls, value: str | None) -> str | None:
        return value or None


class PlannerReadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enjoyment: int
    highlights: str | None
    misses: str | None
    updated_at: dt.datetime


class RatingsOut(BaseModel):
    mine: RatingOut | None
    partner: RatingOut | None
    partner_revealed: bool
    planner_read: PlannerReadOut | None


class PastDateOut(BaseModel):
    id: uuid.UUID
    title: str
    starts_at: dt.datetime | None
    ends_at: dt.datetime | None
    is_surprise: bool
    planner_user_id: uuid.UUID
    mine: RatingOut | None
    partner: RatingOut | None
    planner_read: PlannerReadOut | None
