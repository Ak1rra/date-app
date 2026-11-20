"""Couple, home area, constraints and important-date request and response models."""

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

from app.schemas.auth import is_valid_timezone
from app.schemas.profiles import MAX_ITEMS, MAX_TEXT, Transport, dedupe

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Money = Annotated[float, Field(ge=0, le=99_999_999.99)]


class HomeArea(BaseModel):
    label: str
    lat: float
    lng: float


class MemberOut(BaseModel):
    user_id: uuid.UUID
    display_name: str | None
    role: Literal["owner", "partner"]


class CoupleOut(BaseModel):
    id: uuid.UUID
    name: str | None
    timezone: str
    status: str
    home_area: HomeArea | None
    members: list[MemberOut]
    partner_joined: bool


class CouplePatch(BaseModel):
    # Leaving a field out means "unchanged"; an explicit null is a 422.
    name: Name = None  # type: ignore[assignment]
    timezone: str = None  # type: ignore[assignment]

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        if not is_valid_timezone(value):
            raise ValueError("Timezone must be an IANA zone name such as Europe/Paris.")
        return value


class HomeAreaIn(BaseModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]


class ConstraintsIn(BaseModel):
    """A full replace: anything left out is reset to empty (currency to USD)."""

    budget_min: Money | None = None
    budget_max: Money | None = None
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] = "USD"
    max_travel_minutes: Annotated[int, Field(ge=1, le=1440)] | None = None
    max_radius_km: Annotated[int, Field(ge=1, le=1000)] | None = None
    transport_modes: Annotated[
        list[Transport], Field(max_length=MAX_ITEMS), AfterValidator(dedupe)
    ] = []
    alcohol: Literal["yes", "some", "none"] | None = None
    kids_pets_notes: Annotated[str, StringConstraints(max_length=MAX_TEXT)] | None = None

    @model_validator(mode="after")
    def _budget_ordered(self) -> Self:
        if (
            self.budget_min is not None
            and self.budget_max is not None
            and self.budget_min > self.budget_max
        ):
            raise ValueError("budget_min must not be more than budget_max.")
        return self


class ConstraintsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    budget_min: float | None
    budget_max: float | None
    currency: str
    max_travel_minutes: int | None
    max_radius_km: int | None
    transport_modes: list[str]
    alcohol: str | None
    kids_pets_notes: str | None
    updated_at: dt.datetime
    updated_by_user_id: uuid.UUID | None


DateKind = Literal["anniversary", "birthday", "custom"]


class ImportantDateIn(BaseModel):
    name: Name
    date: dt.date
    recurring: bool = False
    kind: DateKind
    profile_id: uuid.UUID | None = None


class ImportantDatePatch(BaseModel):
    """Only the fields present are changed."""

    name: Name = None  # type: ignore[assignment]
    date: dt.date = None  # type: ignore[assignment]
    recurring: bool = None  # type: ignore[assignment]
    kind: DateKind = None  # type: ignore[assignment]
    profile_id: uuid.UUID | None = None  # null clears the link


class ImportantDateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    date: dt.date
    recurring: bool
    kind: DateKind
    profile_id: uuid.UUID | None
