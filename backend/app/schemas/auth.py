"""Auth and /me request and response models."""

import uuid
from functools import cache
from typing import Any
from zoneinfo import available_timezones

from pydantic import BaseModel, EmailStr, field_validator


@cache
def _timezone_names() -> frozenset[str]:
    return frozenset(available_timezones())


def is_valid_timezone(name: str) -> bool:
    """True for an IANA zone name such as `Europe/Paris` (exact case, as stored)."""
    return name in _timezone_names()


class MagicLinkIn(BaseModel):
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def _length(cls, value: str) -> str:
        if len(value) > 320:
            raise ValueError("Email address is too long.")
        return value


class VerifyIn(BaseModel):
    token: str
    # An unusable value is ignored (the account falls back to UTC), never a validation error.
    timezone: str | None = None


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    timezone: str
    couple_id: uuid.UUID | None
    role: str | None


class SessionOut(BaseModel):
    authenticated: bool
    user: MeOut | None


class OkOut(BaseModel):
    ok: bool


class MePatch(BaseModel):
    display_name: str | None = None
    timezone: str | None = None

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not 1 <= len(value) <= 120:
            raise ValueError("Display name must be 1 to 120 characters.")
        return value

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_timezone(value):
            raise ValueError("Timezone must be an IANA zone name such as Europe/Paris.")
        return value
