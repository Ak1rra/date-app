"""The redacted surprise view: all the surprisee gets before the reveal (m4 design)."""

import datetime as dt
import uuid

from pydantic import BaseModel


class ReleasedHintOut(BaseModel):
    id: uuid.UUID
    kind: str
    text: str
    released_at: dt.datetime


class SurpriseOut(BaseModel):
    id: uuid.UUID
    starts_at: dt.datetime
    ends_at: dt.datetime | None
    reveal_at: dt.datetime | None
    hints: list[ReleasedHintOut]
