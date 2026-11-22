"""Notification centre, settings and Web Push request/response models."""

import datetime as dt
import re
import uuid
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, TypeAdapter, field_validator

KINDS = (
    "reminder_day_before",
    "reminder_hour_before",
    "checklist_due",
    "match",
    "ideas_added",
    "date_announced",
    "date_rescheduled",
    "date_cancelled",
    "hint_released",
    "surprise_revealed",
    "partner_reaction",
    "rate_prompt",
    "plan_nudge",
)

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_HTTP_URL = TypeAdapter(AnyHttpUrl)


def _time_string(value: str) -> str:
    if not _TIME_RE.fullmatch(value):
        raise ValueError("Time must be in HH:MM 24-hour format.")
    return value


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    title: str
    body: str
    data: dict[str, Any]
    deliver_after: dt.datetime
    created_at: dt.datetime
    pushed_at: dt.datetime | None
    read_at: dt.datetime | None


class NotificationSettings(BaseModel):
    kinds: dict[str, bool]
    quiet_start: str
    quiet_end: str

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def _times(cls, value: str) -> str:
        return _time_string(value)


class NotificationSettingsPatch(BaseModel):
    # A default of None keeps omitted fields distinct while the non-optional annotations reject
    # an explicit JSON null, matching the other PATCH-style schemas in this project.
    kinds: dict[str, bool] = None  # type: ignore[assignment]
    quiet_start: str = None  # type: ignore[assignment]
    quiet_end: str = None  # type: ignore[assignment]

    @field_validator("kinds")
    @classmethod
    def _known_kinds(cls, value: dict[str, bool]) -> dict[str, bool]:
        unknown = set(value) - set(KINDS)
        if unknown:
            raise ValueError(f"Unknown notification kind: {sorted(unknown)[0]}.")
        return value

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def _times(cls, value: str) -> str:
        return _time_string(value)


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=500)
    auth: str = Field(min_length=1, max_length=500)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)
    keys: PushKeys

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("Push endpoint must be an HTTPS URL.")
        try:
            _HTTP_URL.validate_python(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Push endpoint must be an HTTPS URL.") from exc
        return value


class PushConfigOut(BaseModel):
    public_key: str | None


class OkOut(BaseModel):
    ok: bool


class UnreadCountOut(BaseModel):
    count: int


class UpdatedOut(BaseModel):
    updated: int
