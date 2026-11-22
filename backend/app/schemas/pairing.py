"""Pairing request and response models: invites, join and leave (FR-PAIR-1..3)."""

import datetime as dt
from typing import Annotated, Self

from pydantic import BaseModel, StringConstraints, model_validator

Token = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ShortCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12)]


class InviteOut(BaseModel):
    link: str
    short_code: str
    expires_at: dt.datetime


class InviteCurrentOut(BaseModel):
    short_code: str
    expires_at: dt.datetime


class JoinIn(BaseModel):
    """Exactly one of `token` (from the invite link) or `short_code` (typed in)."""

    token: Token | None = None
    short_code: ShortCode | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.token is None) == (self.short_code is None):
            raise ValueError("Send either token or short_code.")
        return self
