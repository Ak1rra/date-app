import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient, get_ai
from app.api.v1.itineraries import get_itinerary_providers
from app.config import Settings
from app.db import get_session
from app.deps import CurrentUser, current_user, get_app_settings
from app.models import PlanTemplate
from app.providers.registry import Providers
from app.schemas.itineraries import ItineraryOut
from app.schemas.template import TemplateIn, TemplateOut, TemplateReplanIn
from app.services import itineraries as itinerary_service
from app.services import templates as service
from app.services.couple import require_couple_id

router = APIRouter(tags=["templates"])


async def own_template(
    template_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> PlanTemplate:
    """The caller's own template, else 404 — resolved before the AI and provider dependencies,
    so nobody else learns anything (not even that the planner is unavailable)."""
    return await service.get_own_template(
        session, require_couple_id(current.couple_id), current.user.id, template_id
    )


@router.post("/itineraries/{itinerary_id}/template", response_model=TemplateOut, status_code=201)
async def save_template(
    itinerary_id: uuid.UUID,
    body: TemplateIn,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> TemplateOut:
    template = await service.save_template(
        session, require_couple_id(current.couple_id), current.user.id, itinerary_id, body.name
    )
    await session.commit()
    return service.template_out(template)


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> list[TemplateOut]:
    rows = await service.list_templates(
        session, require_couple_id(current.couple_id), current.user.id
    )
    return [service.template_out(t) for t in rows]


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(
    template_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> None:
    await service.delete_template(
        session, require_couple_id(current.couple_id), current.user.id, template_id
    )
    await session.commit()


@router.post("/templates/{template_id}/replan", response_model=ItineraryOut, status_code=201)
async def replan(
    body: TemplateReplanIn,
    request: Request,
    template: PlanTemplate = Depends(own_template),
    ai: AIClient = Depends(get_ai),
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ItineraryOut:
    itinerary = await service.replan(
        session,
        ai=ai,
        providers=providers,
        settings=settings,
        sessionmaker=request.app.state.sessionmaker,
        couple_id=require_couple_id(current.couple_id),
        requester_user_id=current.user.id,
        template_id=template.id,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
    )
    await session.commit()
    return await itinerary_service.itinerary_out(
        session, itinerary, current.user.id, providers, settings=settings
    )
