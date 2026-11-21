"""Response models for the Google Routes API computeRoutes call (the fields in the field mask)."""

import re
from typing import Annotated, Any

from pydantic import BeforeValidator, Field

from app.providers.schema import NonNegative, ResponseModel

_DURATION = re.compile(r"\d+(\.\d+)?s")


def _seconds(value: Any) -> float:
    """Routes durations are protobuf strings such as "165s"."""
    if not isinstance(value, str) or not _DURATION.fullmatch(value):
        raise ValueError('a duration looks like "165s"')
    return float(value.removesuffix("s"))


Seconds = Annotated[NonNegative, BeforeValidator(_seconds)]


class Route(ResponseModel):
    duration: Seconds
    distanceMeters: NonNegative | None = None


class RoutesResponse(ResponseModel):
    """Routes leaves `routes` out when there is no route."""

    routes: list[Route] = Field(default_factory=list)
