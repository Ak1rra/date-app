"""Planner note request and response models."""

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

NoteKind = Literal["note", "mentioned_idea"]
NoteText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class NoteIn(BaseModel):
    about_profile_id: uuid.UUID
    kind: NoteKind
    text: NoteText


class NotePatch(BaseModel):
    # Leaving a field out means "unchanged" (defaults are not validated); an explicit null is a
    # 422, and the schema does not advertise null.
    text: NoteText = None  # type: ignore[assignment]
    archived: bool = None  # type: ignore[assignment]


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    about_profile_id: uuid.UUID
    kind: NoteKind
    text: str
    created_at: dt.datetime
    updated_at: dt.datetime
    archived_at: dt.datetime | None
