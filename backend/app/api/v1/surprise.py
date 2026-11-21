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
from app.schemas.surprise import HintIn, HintOut, HintPatch, HintSuggestionsOut, RevealAtIn
from app.services import itineraries as itinerary_service
from app.services import surprise as service
from app.services import surprise_ai
from app.services.couple import now, require_couple_id
from app.services.itinerary_access import get_editable

router = APIRouter(prefix="/itineraries", tags=["surprise"])


async def surprise_itinerary(
    itinerary_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> Itinerary:
    """The caller's own draft or scheduled surprise: 404 hidden, 403 partner, 409 otherwise."""
    itinerary = await get_editable(
        session, current.user.id, require_couple_id(current.couple_id), itinerary_id
    )
    service.require_surprise_editable(itinerary)
    return itinerary


async def _settle(session: AsyncSession, itinerary: Itinerary, settings: Settings) -> None:
    """Process what is already due and queue jobs for what is not; the caller commits."""
    moment = now()
    await service.process_due(session, itinerary, settings, moment)
    await service.schedule_due_jobs(session, itinerary, moment)


@router.get("/{itinerary_id}/hints", response_model=list[HintOut])
async def list_hints(
    itinerary: Itinerary = Depends(surprise_itinerary),
    session: AsyncSession = Depends(get_session),
) -> list[HintOut]:
    return await service.list_hints(session, itinerary)


@router.post("/{itinerary_id}/hints/suggest", response_model=HintSuggestionsOut)
async def suggest_hints(
    request: Request,
    itinerary: Itinerary = Depends(surprise_itinerary),
    providers: Providers = Depends(get_itinerary_providers),
    ai: AIClient = Depends(get_ai),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> HintSuggestionsOut:
    """Suggestions only: nothing is saved; the planner adds the ones they like with POST /hints."""
    suggestions = await surprise_ai.suggest_hints(
        session,
        ai=ai,
        providers=providers,
        settings=settings,
        sessionmaker=request.app.state.sessionmaker,
        user_id=current.user.id,
        itinerary=itinerary,
    )
    return HintSuggestionsOut(suggestions=suggestions)


@router.post("/{itinerary_id}/hints", response_model=HintOut, status_code=201)
async def create_hint(
    body: HintIn,
    itinerary: Itinerary = Depends(surprise_itinerary),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_session),
) -> HintOut:
    hint = await service.create_hint(session, itinerary, body)
    await _settle(session, itinerary, settings)
    out = HintOut.model_validate(hint)
    await session.commit()
    return out


@router.patch("/{itinerary_id}/hints/{hint_id}", response_model=HintOut)
async def patch_hint(
    hint_id: uuid.UUID,
    body: HintPatch,
    itinerary: Itinerary = Depends(surprise_itinerary),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_session),
) -> HintOut:
    hint = await service.patch_hint(session, itinerary, hint_id, body)
    await _settle(session, itinerary, settings)
    out = HintOut.model_validate(hint)
    await session.commit()
    return out


@router.delete("/{itinerary_id}/hints/{hint_id}", status_code=204)
async def delete_hint(
    hint_id: uuid.UUID,
    itinerary: Itinerary = Depends(surprise_itinerary),
    session: AsyncSession = Depends(get_session),
) -> None:
    await service.delete_hint(session, itinerary, hint_id)
    await session.commit()


@router.put("/{itinerary_id}/reveal-at", response_model=ItineraryOut)
async def set_reveal_at(
    body: RevealAtIn,
    itinerary: Itinerary = Depends(surprise_itinerary),
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ItineraryOut:
    await service.set_reveal_at(session, itinerary, body)
    await _settle(session, itinerary, settings)
    out = await itinerary_service.itinerary_out(
        session, itinerary, current.user.id, providers, settings=settings
    )
    await session.commit()
    return out


@router.post("/{itinerary_id}/reveal", response_model=ItineraryOut)
async def reveal_itinerary(
    itinerary: Itinerary = Depends(surprise_itinerary),
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ItineraryOut:
    await service.reveal_now(session, itinerary, settings)
    out = await itinerary_service.itinerary_out(
        session, itinerary, current.user.id, providers, settings=settings
    )
    await session.commit()
    return out
