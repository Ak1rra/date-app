"""Template request and response models (FR-PLAN-8)."""

import datetime as dt
import uuid
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, StringConstraints

TemplateName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]


class TemplateIn(BaseModel):
    name: TemplateName


class TemplateReplanIn(BaseModel):
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: dt.datetime
    source_itinerary_id: uuid.UUID | None
    title: str
    stop_titles: list[str]
