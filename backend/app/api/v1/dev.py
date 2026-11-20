from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import Settings
from app.deps import get_app_settings
from app.errors import ApiError
from app.providers.email.console import OUTBOX

router = APIRouter(prefix="/dev", tags=["dev"])


class OutboxEntry(BaseModel):
    to: str
    subject: str
    link: str | None
    sent_at: datetime


@router.get("/outbox", response_model=list[OutboxEntry])
async def outbox(settings: Settings = Depends(get_app_settings)) -> list[OutboxEntry]:
    """DEV_OUTBOX only: the console email sender's outbox, newest first, at most 50."""
    # It holds sign-in links and is unauthenticated, so it is off by default: DEMO_MODE
    # alone does not enable it.
    if not settings.dev_outbox:
        raise ApiError(404, "not_found", "Not found.")
    return [OutboxEntry(**entry) for entry in reversed(OUTBOX)]
