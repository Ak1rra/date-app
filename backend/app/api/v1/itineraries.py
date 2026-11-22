import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import get_session
from app.deps import CurrentUser, current_user, get_app_settings
from app.providers.registry import Providers, build_providers
from app.schemas.itineraries import (
    ChecklistIn,
    ChecklistItemOut,
    ChecklistPatch,
    ItineraryOut,
    ItineraryPatch,
    ItineraryStatus,
    ItinerarySummary,
    ReactIn,
    ScheduleIn,
    StopsPut,
)
from app.services import itineraries as service
from app.services.couple import require_couple_id
from app.services.itinerary_access import get_editable, get_visible

router = APIRouter(prefix="/itineraries", tags=["itineraries"])


def get_itinerary_providers(
    request: Request,
    settings: Settings = Depends(get_app_settings),
    current: CurrentUser = Depends(current_user),
) -> Providers:
    """One provider bundle per request, so its run memo dedupes repeat place lookups. Tests
    override this dependency."""
    return build_providers(
        settings, couple_id=current.couple_id, session_factory=request.app.state.sessionmaker
    )


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    return value.replace(tzinfo=dt.UTC) if value is not None and value.tzinfo is None else value


@router.get("", response_model=list[ItinerarySummary])
async def list_itineraries(
    status: ItineraryStatus | None = None,
    from_: dt.datetime | None = Query(None, alias="from"),
    to: dt.datetime | None = None,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> list[ItinerarySummary]:
    """The dates the caller may see (never a hidden surprise or an unannounced draft)."""
    return await service.list_summaries(
        session,
        current.user.id,
        require_couple_id(current.couple_id),
        status=status,
        starts_from=_aware(from_),
        starts_to=_aware(to),
    )


@router.get("/{itinerary_id}", response_model=ItineraryOut)
async def get_itinerary(
    itinerary_id: uuid.UUID,
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ItineraryOut:
    itinerary = await get_visible(
        session, current.user.id, require_couple_id(current.couple_id), itinerary_id
    )
    await service.mark_seen(session, itinerary, current.user.id)
    out = await service.itinerary_out(
        session, itinerary, current.user.id, providers, settings=settings
    )
    await session.commit()
    return out


@router.patch("/{itinerary_id}", response_model=ItineraryOut)
async def patch_itinerary(
    itinerary_id: uuid.UUID,
    body: ItineraryPatch,
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ItineraryOut:
    itinerary = await get_editable(
        session, current.user.id, require_couple_id(current.couple_id), itinerary_id
    )
    await service.patch_itinerary(session, itinerary, body, providers, settings)
    out = await service.itinerary_out(
        session, itinerary, current.user.id, providers, settings=settings
    )
    await session.commit()
    return out


@router.put("/{itinerary_id}/stops", response_model=ItineraryOut)
async def put_stops(
    itinerary_id: uuid.UUID,
    body: StopsPut,
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ItineraryOut:
    itinerary = await get_editable(
        session, current.user.id, require_couple_id(current.couple_id), itinerary_id
    )
    await service.replace_stops(session, itinerary, body, providers, settings)
    out = await service.itinerary_out(
        session, itinerary, current.user.id, providers, settings=settings
    )
    await session.commit()
    return out


@router.post("/{itinerary_id}/checklist", response_model=ChecklistItemOut, status_code=201)
async def add_checklist_item(
    itinerary_id: uuid.UUID,
    body: ChecklistIn,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ChecklistItemOut:
    itinerary = await get_editable(
        session, current.user.id, require_couple_id(current.couple_id), itinerary_id
    )
    out = await service.add_checklist_item(session, itinerary, body)
    await session.commit()
    return out


@router.patch("/{itinerary_id}/checklist/{item_id}", response_model=ChecklistItemOut)
async def patch_checklist_item(
    itinerary_id: uuid.UUID,
    item_id: uuid.UUID,
    body: ChecklistPatch,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ChecklistItemOut:
    itinerary = await get_editable(
        session, current.user.id, require_couple_id(current.couple_id), itinerary_id
    )
    out = await service.patch_checklist_item(session, itinerary, item_id, body)
    await session.commit()
    return out


@router.delete("/{itinerary_id}/checklist/{item_id}", status_code=204)
async def delete_checklist_item(
    itinerary_id: uuid.UUID,
    item_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> None:
    itinerary = await get_editable(
        session, current.user.id, require_couple_id(current.couple_id), itinerary_id
    )
    await service.delete_checklist_item(session, itinerary, item_id)
    await session.commit()


@router.post("/{itinerary_id}/schedule", response_model=ItineraryOut)
async def schedule_itinerary(
    itinerary_id: uuid.UUID,
    body: ScheduleIn,
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ItineraryOut:
    itinerary = await get_editable(
        session, current.user.id, require_couple_id(current.couple_id), itinerary_id
    )
    await service.schedule(session, itinerary, body, providers, settings)
    out = await service.itinerary_out(
        session, itinerary, current.user.id, providers, settings=settings
    )
    await session.commit()
    return out


@router.post("/{itinerary_id}/cancel", response_model=ItineraryOut)
async def cancel_itinerary(
    itinerary_id: uuid.UUID,
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ItineraryOut:
    itinerary = await get_editable(
        session, current.user.id, require_couple_id(current.couple_id), itinerary_id
    )
    await service.cancel(session, itinerary, settings)
    out = await service.itinerary_out(
        session, itinerary, current.user.id, providers, settings=settings
    )
    await session.commit()
    return out


@router.post("/{itinerary_id}/react", response_model=ItineraryOut)
async def react_to_itinerary(
    itinerary_id: uuid.UUID,
    body: ReactIn,
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ItineraryOut:
    itinerary = await get_visible(
        session, current.user.id, require_couple_id(current.couple_id), itinerary_id
    )
    await service.react(session, itinerary, current.user.id, body, settings)
    out = await service.itinerary_out(
        session, itinerary, current.user.id, providers, settings=settings
    )
    await session.commit()
    return out
