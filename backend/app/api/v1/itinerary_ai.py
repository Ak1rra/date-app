import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient, get_ai
from app.api.v1.itineraries import get_itinerary_providers
from app.config import Settings
from app.db import get_session
from app.deps import CurrentUser, current_user, get_app_settings
from app.models import Itinerary
from app.providers.registry import Providers
from app.schemas.itineraries import ItineraryOut
from app.schemas.itinerary_ai import PolishIn, PolishOut, RegenerateStopIn
from app.services import itineraries as itinerary_service
from app.services import itinerary_ai as service
from app.services.couple import require_couple_id

router = APIRouter(prefix="/itineraries", tags=["itineraries"])


async def editable(
    itinerary_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> Itinerary:
    """The caller's own draft or scheduled itinerary, else 404/403/409: declared before the AI
    dependency, so nobody learns anything else (not even that the planner is unavailable)."""
    return await service.editable_itinerary(
        session, current.user.id, require_couple_id(current.couple_id), itinerary_id
    )


@router.post("/{itinerary_id}/stops/{stop_id}/regenerate", response_model=ItineraryOut)
async def regenerate_stop(
    stop_id: uuid.UUID,
    request: Request,
    body: RegenerateStopIn | None = None,
    itinerary: Itinerary = Depends(editable),
    ai: AIClient = Depends(get_ai),
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ItineraryOut:
    itinerary = await service.regenerate_stop(
        session,
        ai=ai,
        providers=providers,
        settings=settings,
        sessionmaker=request.app.state.sessionmaker,
        user_id=current.user.id,
        itinerary=itinerary,
        stop_id=stop_id,
        note=body.note if body else None,
    )
    await session.commit()
    return await itinerary_service.itinerary_out(
        session, itinerary, current.user.id, providers, settings=settings
    )


@router.post("/{itinerary_id}/polish-invitation", response_model=PolishOut)
async def polish_invitation(
    body: PolishIn,
    request: Request,
    itinerary: Itinerary = Depends(editable),
    ai: AIClient = Depends(get_ai),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> PolishOut:
    text = await service.polish_invitation(
        session,
        ai=ai,
        settings=settings,
        sessionmaker=request.app.state.sessionmaker,
        user_id=current.user.id,
        itinerary=itinerary,
        draft=body.draft,
    )
    return PolishOut(text=text)
