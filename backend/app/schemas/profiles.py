"""Partner profile and availability request and response models."""

import datetime as dt
import uuid
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Category = Literal[
    "food",
    "outdoors",
    "arts",
    "music",
    "sports",
    "games",
    "learning",
    "nightlife",
    "wellness",
    "travel",
    "shopping",
    "nature",
    "culture",
    "adventure",
    "relaxing",
]
Dietary = Literal[
    "vegetarian",
    "vegan",
    "pescatarian",
    "gluten_free",
    "dairy_free",
    "halal",
    "kosher",
    "nut_free",
    "low_carb",
]
Transport = Literal["car", "transit", "walk", "rideshare", "bike"]

MAX_ITEMS = 30
MAX_TEXT = 2000


def dedupe[T](values: list[T]) -> list[T]:
    """Drop repeats, keeping the first occurrence of each in order."""
    return list(dict.fromkeys(values))


Free = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
CategoryList = Annotated[list[Category], Field(max_length=MAX_ITEMS), AfterValidator(dedupe)]
DietaryList = Annotated[list[Dietary], Field(max_length=MAX_ITEMS), AfterValidator(dedupe)]
FreeList = Annotated[list[Free], Field(max_length=MAX_ITEMS), AfterValidator(dedupe)]
Text = Annotated[str, StringConstraints(max_length=MAX_TEXT)]
Scale = Annotated[int, Field(ge=1, le=5)]


class ProfileIn(BaseModel):
    """A full replace: anything left out is reset to empty."""

    display_name: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] | None
    ) = None
    liked_categories: CategoryList = []
    disliked_categories: CategoryList = []
    cuisines_liked: FreeList = []
    cuisines_disliked: FreeList = []
    energy: Scale | None = None
    indoor_outdoor: Literal["indoor", "outdoor", "either"] | None = None
    social_setting: Scale | None = None
    loves: Text | None = None
    hard_nos: Text | None = None
    dietary: DietaryList = []
    allergies: FreeList = []
    accessibility: Text | None = None


class ProfileOut(BaseModel):
    id: uuid.UUID
    slot: Literal["owner", "partner"]
    is_me: bool
    subject_joined: bool
    can_edit: bool
    display_name: str | None
    liked_categories: list[str]
    disliked_categories: list[str]
    cuisines_liked: list[str]
    cuisines_disliked: list[str]
    energy: int | None
    indoor_outdoor: str | None
    social_setting: int | None
    loves: str | None
    hard_nos: str | None
    dietary: list[str]
    allergies: list[str]
    accessibility: str | None
    edited_by_user_id: uuid.UUID | None
    confirmed_by_subject_at: dt.datetime | None
    updated_at: dt.datetime


class WindowIO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    weekday: Annotated[int, Field(ge=0, le=6)]
    start_time: dt.time
    end_time: dt.time

    @field_validator("start_time", "end_time")
    @classmethod
    def _naive(cls, value: dt.time) -> dt.time:
        if value.tzinfo is not None:
            raise ValueError("Times are local clock times and take no UTC offset.")
        return value

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end_time <= self.start_time:
            raise ValueError("A window must end after it starts.")
        return self


class BlockIO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: dt.date
    note: Annotated[str, StringConstraints(max_length=500)] | None = None


class AvailabilityIO(BaseModel):
    """Both directions: PUT replaces everything with this, GET returns it."""

    windows: Annotated[list[WindowIO], Field(max_length=100)] = []
    blocks: Annotated[list[BlockIO], Field(max_length=500)] = []
