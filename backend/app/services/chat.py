"""The AI chat: threads, message history and applying the actions the assistant proposed
(AI-CHAT). Private threads belong to one member; the shared thread to the couple.

Functions flush but leave the commit to the caller.
"""

import uuid
from typing import Any, cast

from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.caps import SessionMaker
from app.ai.client import get_ai_client
from app.config import Settings
from app.errors import ApiError
from app.models import ChatMessage, ChatThread
from app.providers.registry import Providers
from app.schemas.chat import (
    ChatActionApplyOut,
    ChatActionOut,
    ChatMessageOut,
    ChatMessagesOut,
)
from app.schemas.ideas import IdeaIn, IdeaOut
from app.schemas.itineraries import ItineraryOut, ScheduleIn, StopIn, StopsPut
from app.schemas.plan import IdeaItineraryIn
from app.services import ideas as idea_service
from app.services import itineraries as itinerary_service
from app.services import planning
from app.services.couple import not_found, now
from app.services.itinerary_access import get_editable


def _visible_to(couple_id: uuid.UUID, me: uuid.UUID) -> Any:
    """My private thread or the couple's shared thread."""
    return and_(
        ChatThread.couple_id == couple_id,
        or_(
            and_(ChatThread.scope == "private", ChatThread.owner_user_id == me),
            ChatThread.scope == "shared",
        ),
    )


def _not_hidden_from(me: uuid.UUID) -> Any:
    return or_(ChatMessage.hidden_for_user_id.is_(None), ChatMessage.hidden_for_user_id != me)


async def _find(
    session: AsyncSession, couple_id: uuid.UUID, scope: str, owner: uuid.UUID | None
) -> ChatThread | None:
    query = select(ChatThread).where(ChatThread.couple_id == couple_id, ChatThread.scope == scope)
    if owner is not None:
        query = query.where(ChatThread.owner_user_id == owner)
    return (await session.execute(query)).scalar_one_or_none()


async def _ensure(
    session: AsyncSession, couple_id: uuid.UUID, scope: str, owner: uuid.UUID | None
) -> ChatThread:
    """The thread, created on first use. The insert skips on a conflict with the partial unique
    indexes, so concurrent first use is safe: whoever lost the race reads the winner's row."""
    thread = await _find(session, couple_id, scope, owner)
    if thread is None:
        await session.execute(
            pg_insert(ChatThread)
            .values(id=uuid.uuid4(), couple_id=couple_id, scope=scope, owner_user_id=owner)
            .on_conflict_do_nothing()
        )
        thread = await _find(session, couple_id, scope, owner)
    if thread is None:  # pragma: no cover - a conflicting row is committed by the time we read
        raise ApiError(409, "conflict", "Try that again.")
    return thread


async def get_or_create_threads(
    session: AsyncSession, couple_id: uuid.UUID, me: uuid.UUID
) -> list[ChatThread]:
    """My private thread, then the shared one once the couple has two members."""
    threads = [await _ensure(session, couple_id, "private", me)]
    if await idea_service.partner_user_id(session, couple_id, me) is not None:
        threads.append(await _ensure(session, couple_id, "shared", None))
    return threads


async def get_thread(
    session: AsyncSession, couple_id: uuid.UUID, me: uuid.UUID, thread_id: uuid.UUID
) -> ChatThread:
    """The thread, or 404 unless it is my private thread or the couple's shared one."""
    thread = (
        await session.execute(
            select(ChatThread).where(ChatThread.id == thread_id, _visible_to(couple_id, me))
        )
    ).scalar_one_or_none()
    if thread is None:
        raise not_found("That chat isn't here.")
    return thread


def action_out(action: dict[str, Any]) -> ChatActionOut:
    return ChatActionOut.model_validate(action)


def message_out(message: ChatMessage, me: uuid.UUID) -> ChatMessageOut:
    return ChatMessageOut(
        id=message.id,
        role=cast("Any", message.role),
        author_user_id=message.author_user_id,
        author_is_me=message.author_user_id == me,
        content=message.content,
        actions=[action_out(a) for a in message.actions],
        created_at=message.created_at,
    )


async def list_messages(
    session: AsyncSession,
    thread: ChatThread,
    me: uuid.UUID,
    *,
    before: uuid.UUID | None,
    limit: int,
) -> ChatMessagesOut:
    """The `limit` newest messages strictly older than `before`, oldest first. Messages hidden
    from me are left out."""
    query = select(ChatMessage).where(ChatMessage.thread_id == thread.id, _not_hidden_from(me))
    if before is not None:
        anchor = (
            await session.execute(
                select(ChatMessage.created_at).where(
                    ChatMessage.id == before, ChatMessage.thread_id == thread.id
                )
            )
        ).scalar_one_or_none()
        if anchor is None:
            raise not_found("That message isn't here.")
        query = query.where(tuple_(ChatMessage.created_at, ChatMessage.id) < (anchor, before))
    newest_first = query.order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
    rows = list((await session.execute(newest_first.limit(limit + 1))).scalars())
    page = rows[:limit]
    return ChatMessagesOut(
        messages=[message_out(m, me) for m in reversed(page)], has_more=len(rows) > limit
    )


# --- Applying an action ---------------------------------------------------------------------


async def apply_action(
    session: AsyncSession,
    *,
    providers: Providers,
    settings: Settings,
    sessionmaker: SessionMaker,
    couple_id: uuid.UUID,
    me: uuid.UUID,
    action_id: uuid.UUID,
) -> ChatActionApplyOut:
    """Apply a proposed action through the normal services, once. The applier must be able to
    see the thread; a second apply returns the stored action and changes nothing."""
    found = (
        await session.execute(
            select(ChatMessage, ChatThread)
            .join(ChatThread, ChatThread.id == ChatMessage.thread_id)
            .where(
                ChatMessage.actions.contains([{"id": str(action_id)}]),
                _not_hidden_from(me),
                _visible_to(couple_id, me),
            )
            .with_for_update(of=ChatMessage)
        )
    ).first()
    if found is None:
        raise not_found("That action isn't here.")
    message, thread = found
    actions = [dict(a) for a in message.actions]
    index = next(i for i, a in enumerate(actions) if a.get("id") == str(action_id))
    action = actions[index]
    if action.get("applied_at") is not None:
        return ChatActionApplyOut(action=action_out(action), idea=None, itinerary=None)

    idea_out: IdeaOut | None = None
    itinerary_out: ItineraryOut | None = None
    payload: dict[str, Any] = action["payload"]
    result: dict[str, Any]
    if action["type"] == "idea":
        idea = await idea_service.create_idea(session, couple_id, me, IdeaIn(**payload))
        idea.source = "chat"
        if thread.scope == "shared":
            idea = await idea_service.share_idea(session, couple_id, me, idea.id)
        couple = await idea_service.get_couple(session, couple_id)
        idea_out = await idea_service.idea_out(session, idea, me, couple)
        result = {"idea_id": str(idea.id)}
    elif action["type"] == "itinerary_change":
        itinerary = await get_editable(session, me, couple_id, uuid.UUID(payload["itinerary_id"]))
        if not payload.get("stops"):
            raise ApiError(409, "nothing_to_apply", "That suggestion has no stops to apply.")
        stops = StopsPut(stops=[StopIn(**stop) for stop in payload["stops"]])
        await itinerary_service.replace_stops(session, itinerary, stops, providers, settings)
        itinerary_out = await itinerary_service.itinerary_out(
            session, itinerary, me, providers, settings=settings
        )
        result = {"itinerary_id": str(itinerary.id)}
    elif action["type"] == "calendar_slot":
        starts_at, ends_at = payload["starts_at"], payload.get("ends_at")
        if payload.get("itinerary_id"):
            itinerary = await get_editable(
                session, me, couple_id, uuid.UUID(payload["itinerary_id"])
            )
            when = ScheduleIn(starts_at=starts_at, ends_at=ends_at, mode="announce")
            await itinerary_service.schedule(session, itinerary, when, providers, settings)
        elif payload.get("idea_id"):
            ai = get_ai_client(settings)
            if ai is None:
                raise ApiError(503, "ai_unavailable", "The planner isn't available right now.")
            itinerary = await planning.build_from_idea(
                session,
                ai=ai,
                providers=providers,
                settings=settings,
                sessionmaker=sessionmaker,
                couple_id=couple_id,
                requester_user_id=me,
                idea_id=uuid.UUID(payload["idea_id"]),
                body=IdeaItineraryIn(starts_at=starts_at, ends_at=ends_at),
            )
        else:
            raise ApiError(409, "nothing_to_apply", "That suggestion has no date to put on.")
        itinerary_out = await itinerary_service.itinerary_out(
            session, itinerary, me, providers, settings=settings
        )
        result = {"itinerary_id": str(itinerary.id)}
    else:  # pragma: no cover - the proposal tools only store the three types above
        raise ApiError(409, "nothing_to_apply", "That suggestion can't be applied.")

    actions[index] = {**action, "applied_at": now().isoformat(), "result": result}
    message.actions = actions  # a new list, so SQLAlchemy sees the JSONB change
    await session.flush()
    return ChatActionApplyOut(
        action=action_out(actions[index]), idea=idea_out, itinerary=itinerary_out
    )


# --- Sending a message ----------------------------------------------------------------------


async def add_message(
    session: AsyncSession,
    thread: ChatThread,
    *,
    role: str,
    author: uuid.UUID | None,
    content: str,
    actions: list[dict[str, Any]] | None = None,
    tool_trace: list[dict[str, Any]] | None = None,
) -> ChatMessage:
    """Store a message and touch the thread; `created_at` is loaded, ready for `message_out`."""
    message = ChatMessage(
        thread_id=thread.id,
        role=role,
        author_user_id=author,
        content=content,
        actions=actions or [],
        tool_trace=tool_trace or [],
    )
    session.add(message)
    thread.updated_at = now()
    await session.flush()
    await session.refresh(message)
    return message
