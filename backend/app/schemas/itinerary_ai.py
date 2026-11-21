from typing import Annotated

from pydantic import BaseModel, StringConstraints

Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
Draft = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class RegenerateStopIn(BaseModel):
    note: Note | None = None


class PolishIn(BaseModel):
    draft: Draft


class PolishOut(BaseModel):
    text: str
