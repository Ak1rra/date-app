import uuid
from typing import Any, cast

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import caps
from app.ai.client import AIClient, Usage, get_ai
from app.api.v1.itineraries import get_itinerary_providers
from app.config import Settings
from app.db import get_session
from app.deps import CurrentUser, current_user, get_app_settings
from app.providers.registry import Providers
from app.schemas.chat import (
    ChatActionApplyOut,
    ChatMessageIn,
    ChatMessagesOut,
    ChatThreadOut,
)
from app.services import chat as service
from app.services import chat_stream
from app.services.couple import require_couple_id

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/threads", response_model=list[ChatThreadOut])
async def list_threads(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> list[ChatThreadOut]:
    """My private thread, and the shared one once my partner has joined. Created on first use."""
    threads = await service.get_or_create_threads(
        session, require_couple_id(current.couple_id), current.user.id
    )
    await session.commit()
    return [
        ChatThreadOut(id=t.id, scope=cast("Any", t.scope), title=t.title, updated_at=t.updated_at)
        for t in threads
    ]


@router.get("/threads/{thread_id}/messages", response_model=ChatMessagesOut)
async def list_messages(
    thread_id: uuid.UUID,
    before: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ChatMessagesOut:
    """A page of messages, oldest to newest; pass the first id back as `before` for older ones."""
    thread = await service.get_thread(
        session, require_couple_id(current.couple_id), current.user.id, thread_id
    )
    return await service.list_messages(session, thread, current.user.id, before=before, limit=limit)


@router.post("/threads/{thread_id}/messages")
async def send_message(
    thread_id: uuid.UUID,
    body: ChatMessageIn,
    request: Request,
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    ai: AIClient = Depends(get_ai),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> chat_stream.ChatStreamResponse:
    """Send a message and stream the assistant's reply as Server-Sent Events: `ack` (my stored
    message), then `delta`, `status` and `action` events, then `done` or `error`. Problems found
    before the stream starts (404, 429) are ordinary JSON errors and store nothing."""
    couple_id, me = require_couple_id(current.couple_id), current.user.id
    sessionmaker = request.app.state.sessionmaker
    thread = await service.get_thread(session, couple_id, me, thread_id)
    reservation = await caps.reserve(
        sessionmaker, settings, couple_id=couple_id, user_id=me, feature="chat"
    )
    settler = chat_stream.Settler(sessionmaker, settings, reservation)
    try:
        message = await service.add_message(
            session, thread, role="user", author=me, content=body.content
        )
        user_message = service.message_out(message, me).model_dump(mode="json")
        chat_stream.notify_shared(session, thread, couple_id, me)
        await session.commit()
    except BaseException:
        await settler.settle(False, Usage(model=""), 0)
        raise
    return chat_stream.ChatStreamResponse(
        chat_stream.stream_reply(
            ai=ai,
            providers=providers,
            sessionmaker=sessionmaker,
            couple_id=couple_id,
            me=me,
            thread_id=thread_id,
            user_message=user_message,
            settler=settler,
        ),
        settler,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/actions/{action_id}/apply", response_model=ChatActionApplyOut)
async def apply_action(
    action_id: uuid.UUID,
    request: Request,
    settings: Settings = Depends(get_app_settings),
    providers: Providers = Depends(get_itinerary_providers),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ChatActionApplyOut:
    """Apply what the assistant proposed, once; applying again returns the stored result."""
    out = await service.apply_action(
        session,
        providers=providers,
        settings=settings,
        sessionmaker=request.app.state.sessionmaker,
        couple_id=require_couple_id(current.couple_id),
        me=current.user.id,
        action_id=action_id,
    )
    await session.commit()
    return out
