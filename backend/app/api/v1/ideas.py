import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIClient, get_ai
from app.api.v1.itineraries import get_itinerary_providers
from app.config import Settings
from app.db import get_session
from app.deps import CurrentUser, current_user, get_app_settings
from app.models import Idea
from app.providers.registry import Providers
from app.schemas.ideas import (
    DeckOut,
    GenerateIdeasIn,
    GenerateIdeasOut,
    IdeaIn,
    IdeaListsOut,
    IdeaOut,
    IdeaPatch,
    IdeaStatus,
    IdeaVisibility,
    VoteIn,
)
from app.services import ideas as idea_service
from app.services import ideas_ai as ai_service
from app.services.couple import require_couple_id

router = APIRouter(tags=["ideas"])


async def _out(session: AsyncSession, current: CurrentUser, idea: Idea) -> IdeaOut:
    couple = await idea_service.get_couple(session, idea.couple_id)
    return await idea_service.idea_out(session, idea, current.user.id, couple)


@router.get("/ideas", response_model=list[IdeaOut])
async def list_ideas(
    visibility: IdeaVisibility = "private",
    status: IdeaStatus = "active",
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> list[IdeaOut]:
    """`private` is the caller's own ideas, `shared` is the couple's."""
    couple_id = require_couple_id(current.couple_id)
    rows = await idea_service.list_ideas(session, couple_id, current.user.id, visibility, status)
    couple = await idea_service.get_couple(session, couple_id)
    return await idea_service.ideas_out(session, rows, current.user.id, couple)


@router.post("/ideas", response_model=IdeaOut, status_code=201)
async def create_idea(
    body: IdeaIn,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> IdeaOut:
    idea = await idea_service.create_idea(
        session, require_couple_id(current.couple_id), current.user.id, body
    )
    await session.commit()
    return await _out(session, current, idea)


@router.post("/ideas/generate", response_model=GenerateIdeasOut)
async def generate_ideas(
    body: GenerateIdeasIn,
    request: Request,
    ai: AIClient = Depends(get_ai),
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> GenerateIdeasOut:
    """New AI ideas: shared ones for the deck (`match`) or private ones (`plan`). `degraded`
    lists providers that were down."""
    couple_id = require_couple_id(current.couple_id)
    ideas, degraded = await ai_service.generate_ideas(
        session,
        ai=ai,
        providers=providers,
        settings=settings,
        sessionmaker=request.app.state.sessionmaker,
        user_id=current.user.id,
        couple_id=couple_id,
        body=body,
    )
    await session.commit()
    couple = await idea_service.get_couple(session, couple_id)
    out = await idea_service.ideas_out(session, ideas, current.user.id, couple)
    return GenerateIdeasOut(ideas=out, degraded=degraded)


@router.get("/ideas/lists", response_model=IdeaListsOut)
async def idea_lists(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> IdeaListsOut:
    return await idea_service.lists(session, require_couple_id(current.couple_id), current.user.id)


@router.patch("/ideas/{idea_id}", response_model=IdeaOut)
async def patch_idea(
    idea_id: uuid.UUID,
    body: IdeaPatch,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> IdeaOut:
    idea = await idea_service.patch_idea(
        session, require_couple_id(current.couple_id), current.user.id, idea_id, body
    )
    await session.commit()
    return await _out(session, current, idea)


@router.delete("/ideas/{idea_id}", status_code=204)
async def delete_idea(
    idea_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> None:
    """Archives the idea; nothing is deleted."""
    await idea_service.archive_idea(
        session, require_couple_id(current.couple_id), current.user.id, idea_id
    )
    await session.commit()


@router.post("/ideas/{idea_id}/share", response_model=IdeaOut)
async def share_idea(
    idea_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> IdeaOut:
    idea = await idea_service.share_idea(
        session, require_couple_id(current.couple_id), current.user.id, idea_id
    )
    await session.commit()
    return await _out(session, current, idea)


@router.get("/deck", response_model=DeckOut)
async def get_deck(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> DeckOut:
    return await idea_service.deck(session, require_couple_id(current.couple_id), current.user.id)


@router.put("/ideas/{idea_id}/vote", response_model=IdeaOut)
async def vote(
    idea_id: uuid.UUID,
    body: VoteIn,
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> IdeaOut:
    idea = await idea_service.cast_vote(
        session,
        require_couple_id(current.couple_id),
        current.user.id,
        idea_id,
        body.value,
        settings,
    )
    await session.commit()
    return await _out(session, current, idea)


@router.post(
    "/deck/undo", response_model=IdeaOut, responses={204: {"description": "Nothing to undo."}}
)
async def undo(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> IdeaOut | Response:
    """Takes back my latest vote if it is under ten minutes old and not locked."""
    idea = await idea_service.undo_vote(
        session, require_couple_id(current.couple_id), current.user.id
    )
    if idea is None:
        return Response(status_code=204)
    await session.commit()
    return await _out(session, current, idea)
