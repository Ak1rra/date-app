import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import CurrentUser, current_user
from app.errors import ApiError
from app.schemas.feedback import (
    PastDateOut,
    PlannerReadIn,
    PlannerReadOut,
    RatingIn,
    RatingOut,
    RatingsOut,
)
from app.services import feedback as service
from app.services.couple import require_couple_id
from app.services.itinerary_access import get_visible

router = APIRouter(tags=["feedback"])


@router.put("/itineraries/{itinerary_id}/rating", response_model=RatingOut)
async def put_rating(
    itinerary_id: uuid.UUID,
    body: RatingIn,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> RatingOut:
    itinerary = await get_visible(
        session, current.user.id, require_couple_id(current.couple_id), itinerary_id
    )
    rating = await service.put_rating(session, itinerary, current.user.id, body)
    out = RatingOut.model_validate(rating)
    await session.commit()
    return out


@router.get("/itineraries/{itinerary_id}/ratings", response_model=RatingsOut)
async def get_ratings(
    itinerary_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> RatingsOut:
    itinerary = await get_visible(
        session, current.user.id, require_couple_id(current.couple_id), itinerary_id
    )
    return await service.get_ratings(session, itinerary, current.user.id)


@router.put("/itineraries/{itinerary_id}/planner-read", response_model=PlannerReadOut)
async def put_planner_read(
    itinerary_id: uuid.UUID,
    body: PlannerReadIn,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> PlannerReadOut:
    itinerary = await get_visible(
        session, current.user.id, require_couple_id(current.couple_id), itinerary_id
    )
    if itinerary.planner_user_id != current.user.id:
        raise ApiError(403, "forbidden", "Only the person planning this date can add their read.")
    read = await service.put_planner_read(session, itinerary, current.user.id, body)
    out = PlannerReadOut.model_validate(read)
    await session.commit()
    return out


@router.get("/past-dates", response_model=list[PastDateOut])
async def list_past_dates(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> list[PastDateOut]:
    return await service.past_dates(session, current.user.id, require_couple_id(current.couple_id))
