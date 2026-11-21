"""Surprise-mode request and response models (M4): hints and the reveal time."""

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints

HintKind = Literal[
    "dress_code",
    "bring",
    "ready_by",
    "eat",
    "energy",
    "indoor_outdoor",
    "weather",
    "travel",
    "custom",
]

HintText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class HintIn(BaseModel):
    kind: HintKind
    text: HintText
    release_at: AwareDatetime | None = None


class HintPatch(BaseModel):
    kind: HintKind | None = None
    text: HintText | None = None
    release_at: AwareDatetime | None = None


class HintOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: HintKind
    text: str
    release_at: dt.datetime
    released_at: dt.datetime | None


class HintSuggestionOut(BaseModel):
    kind: HintKind
    text: str


class HintSuggestionsOut(BaseModel):
    suggestions: list[HintSuggestionOut]


class RevealAtIn(BaseModel):
    reveal_at: AwareDatetime | None = None
