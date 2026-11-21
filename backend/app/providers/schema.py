"""Building blocks for the response models of the real providers.

Every real provider parses the 2xx body it gets through Pydantic models (one per response shape
it consumes, in its own `schema.py`) rather than by hand. Rules shared by all of them: only the
fields we read are modelled (`extra="ignore"`), a field we need is required, a field the
provider may legitimately leave out is `| None = None`, floats are finite, and a present but
empty object fails when it has required fields. A `pydantic.ValidationError` is a ValueError, so
`RealProvider._call` turns it into ProviderUnavailable(reason "bad_response") and it is never
cached.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

Latitude = Annotated[FiniteFloat, Field(ge=-90, le=90)]
Longitude = Annotated[FiniteFloat, Field(ge=-180, le=180)]
NonNegative = Annotated[FiniteFloat, Field(ge=0)]


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
