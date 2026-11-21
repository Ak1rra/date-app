import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient, get_ai
from app.api.v1.itineraries import get_itinerary_providers
from app.config import Settings
from app.db import get_session
from app.deps import CurrentUser, current_user, get_app_settings
from app.models.plan import PlanSession
from app.providers.registry import Providers
from app.schemas.itineraries import ItineraryOut
from app.schemas.plan import (
    PlanBuildIn,
    PlanOptionsIn,
    PlanOptionsOut,
    PlanSessionIn,
    PlanSessionOut,
)
from app.services import itineraries as itinerary_service
from app.services import planning as service
from app.services.couple import require_couple_id

router = APIRouter(prefix="/plan", tags=["plan"])


@router.post("/sessions", response_model=PlanSessionOut, status_code=201)
async def create_session(
    body: PlanSessionIn,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> PlanSessionOut:
    plan = await service.create_session(
        session,
        couple_id=require_couple_id(current.couple_id),
        planner_user_id=current.user.id,
        body=body,
    )
    await session.commit()
    return service.plan_out(plan)


@router.get("/sessions", response_model=list[PlanSessionOut])
async def list_sessions(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> list[PlanSessionOut]:
    plans = await service.list_plans(session, require_couple_id(current.couple_id), current.user.id)
    return [service.plan_out(p) for p in plans]


@router.get("/sessions/{plan_id}", response_model=PlanSessionOut)
async def get_session_by_id(
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> PlanSessionOut:
    plan = await service.get_plan(
        session, require_couple_id(current.couple_id), current.user.id, plan_id
    )
    return service.plan_out(plan)


async def owned_plan(
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> PlanSession:
    """The caller's own plan session, else 404 — resolved before the AI and provider
    dependencies, so nobody else learns anything (not even that the planner is unavailable)."""
    return await service.get_plan(
        session, require_couple_id(current.couple_id), current.user.id, plan_id
    )


@router.post("/sessions/{plan_id}/options", response_model=PlanOptionsOut)
async def make_options(
    request: Request,
    body: PlanOptionsIn | None = None,
    plan: PlanSession = Depends(owned_plan),
    ai: AIClient = Depends(get_ai),
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> PlanOptionsOut:
    """Three more options (also saved as ideas). `degraded` lists providers that were down."""
    degraded = await service.plan_options(
        session,
        ai=ai,
        providers=providers,
        settings=settings,
        sessionmaker=request.app.state.sessionmaker,
        plan=plan,
        more=body.more if body else False,
    )
    await session.commit()
    return PlanOptionsOut(**service.plan_out(plan).model_dump(), degraded=degraded)


@router.post("/sessions/{plan_id}/build", response_model=ItineraryOut)
async def build(
    body: PlanBuildIn,
    request: Request,
    plan: PlanSession = Depends(owned_plan),
    ai: AIClient = Depends(get_ai),
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ItineraryOut:
    itinerary = await service.build(
        session,
        ai=ai,
        providers=providers,
        settings=settings,
        sessionmaker=request.app.state.sessionmaker,
        plan=plan,
        requester_user_id=current.user.id,
        option_ids=body.option_ids,
        combine_note=body.combine_note,
    )
    await session.commit()
    return await itinerary_service.itinerary_out(
        session, itinerary, current.user.id, providers, settings=settings
    )


@router.post("/sessions/{plan_id}/plan-all", response_model=ItineraryOut)
async def plan_all(
    request: Request,
    plan: PlanSession = Depends(owned_plan),
    ai: AIClient = Depends(get_ai),
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ItineraryOut:
    itinerary = await service.plan_all(
        session,
        ai=ai,
        providers=providers,
        settings=settings,
        sessionmaker=request.app.state.sessionmaker,
        plan=plan,
        requester_user_id=current.user.id,
    )
    await session.commit()
    return await itinerary_service.itinerary_out(
        session, itinerary, current.user.id, providers, settings=settings
    )
