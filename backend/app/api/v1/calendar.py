from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import CurrentUser, current_user
from app.schemas.calendar import CalendarEntry, SuggestTimesIn, SuggestTimesOut
from app.services import calendar as service
from app.services.couple import require_couple_id

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("", response_model=list[CalendarEntry])
async def get_calendar(
    from_: str = Query(alias="from"),
    to: str = Query(),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> list[CalendarEntry]:
    """The dates the caller may see plus the couple's important dates, in a range of at most
    120 days. `from` and `to` are dates or timestamps; a date `to` includes that whole day."""
    return await service.list_entries(
        session, current.user.id, require_couple_id(current.couple_id), from_, to
    )


@router.post("/suggest-times", response_model=SuggestTimesOut)
async def suggest_times(
    body: SuggestTimesIn,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> SuggestTimesOut:
    """Up to three times when both of you are free, soonest first (weekend evenings first when
    the time of day doesn't matter). Deterministic: no AI."""
    return await service.suggest_times(
        session, current.user.id, require_couple_id(current.couple_id), body
    )
