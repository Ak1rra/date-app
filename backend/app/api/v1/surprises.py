import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import CurrentUser, current_user
from app.schemas.surprise_view import SurpriseOut
from app.services import surprise_view as service
from app.services.couple import require_couple_id

router = APIRouter(prefix="/surprises", tags=["surprise"])


@router.get("", response_model=list[SurpriseOut])
async def list_surprises(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> list[SurpriseOut]:
    """The surprises planned for the caller that haven't been revealed: times and released
    hints only, soonest first."""
    return await service.list_surprises(
        session, current.user.id, require_couple_id(current.couple_id)
    )


@router.get("/{itinerary_id}", response_model=SurpriseOut)
async def get_surprise(
    itinerary_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> SurpriseOut:
    return await service.get_surprise(
        session, current.user.id, require_couple_id(current.couple_id), itinerary_id
    )
