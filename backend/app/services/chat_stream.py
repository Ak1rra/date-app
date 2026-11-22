"""Answering a chat message as a stream of Server-Sent Events (AI-CHAT).

The request has already stored the user's message and reserved a run against the caps. The
generator here outlives the request's database session (FastAPI closes it before the body
streams), so it opens its own from the app's sessionmaker.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.ai import caps
from app.ai.caps import SessionMaker
from app.ai.client import (
    AIClient,
    AIError,
    ChatEvent,
    ChatFinished,
    TextDelta,
    ToolStarted,
    Usage,
)
from app.ai.context import build_context, first_name
from app.ai.prompts import CHAT_SYSTEM
from app.ai.tools import ToolExecutor
from app.config import Settings
from app.models import ChatMessage, ChatThread, Couple, CoupleConstraints, PartnerProfile
from app.providers.registry import Providers
from app.providers.types import LatLng
from app.realtime import emit
from app.services import chat as service
from app.services.chat_tools import ChatTools
from app.services.planning import DEFAULT_RADIUS_KM, _recipient_id

logger = logging.getLogger(__name__)

HISTORY_MESSAGES = 30
_FAILED = "The assistant couldn't reply. Try again."

_STATUS_LABELS = {
    "search_places": "Finding places…",
    "get_place_details": "Finding places…",
    "search_events": "Looking for events…",
    "get_weather": "Checking the weather…",
    "get_travel_time": "Working out travel time…",
    "check_calendar": "Checking your calendars…",
    "get_couple_context": "Remembering what you both like…",
}


def status_label(tool: str) -> str:
    if tool.startswith("propose_"):
        return "Drafting a suggestion…"
    return _STATUS_LABELS.get(tool, "Thinking…")


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _turns(
    session: AsyncSession, thread: ChatThread, me: uuid.UUID, couple_id: uuid.UUID
) -> list[dict[str, Any]]:
    """The last messages I can see as user and assistant turns, oldest first. In the shared
    thread a user turn starts with its author's first name. Consecutive turns of one role are
    joined and the list starts with a user turn, as the model requires."""
    # The reply is read by both partners, so in the shared thread nothing hidden from either of
    # them may reach the model.
    visible = (
        ChatMessage.hidden_for_user_id.is_(None)
        if thread.scope == "shared"
        else service._not_hidden_from(me)
    )
    rows = list(
        (
            await session.execute(
                select(ChatMessage)
                .where(ChatMessage.thread_id == thread.id, visible)
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(HISTORY_MESSAGES)
            )
        ).scalars()
    )
    names = {
        user_id: first_name(name)
        for user_id, name in (
            await session.execute(
                select(PartnerProfile.subject_user_id, PartnerProfile.display_name).where(
                    PartnerProfile.couple_id == couple_id
                )
            )
        ).all()
    }
    turns: list[dict[str, Any]] = []
    for message in reversed(rows):
        text = message.content.strip()
        if not text:
            continue
        if message.role == "user" and thread.scope == "shared":
            text = f"{names.get(message.author_user_id, 'Partner')}: {text}"
        if turns and turns[-1]["role"] == message.role:
            turns[-1]["content"] += "\n\n" + text
        elif turns or message.role == "user":
            turns.append({"role": message.role, "content": text})
    return turns


async def _prepare(
    session: AsyncSession,
    *,
    providers: Providers,
    couple_id: uuid.UUID,
    me: uuid.UUID,
    thread: ChatThread,
) -> tuple[str, list[dict[str, Any]], ChatTools]:
    shared = thread.scope == "shared"
    recipient_id = await _recipient_id(session, couple_id, me)
    context = await build_context(session, couple_id, me, recipient_id, shared=shared)
    couple = await session.get(Couple, couple_id)
    assert couple is not None  # the caller is a member
    radius_km = (
        await session.execute(
            select(CoupleConstraints.max_radius_km).where(CoupleConstraints.couple_id == couple_id)
        )
    ).scalar_one_or_none()
    home = (
        LatLng(float(couple.home_lat), float(couple.home_lng))
        if couple.home_lat is not None and couple.home_lng is not None
        else None
    )
    executor = ToolExecutor(
        providers,
        session,
        couple_id=couple_id,
        requester_user_id=me,
        recipient_profile_id=recipient_id,
        home=home,
        timezone=couple.timezone,
        radius_km=float(radius_km or DEFAULT_RADIUS_KM),
        shared=shared,
    )
    return context, await _turns(session, thread, me, couple_id), ChatTools(executor)


class Settler:
    """Finalizes the caps reservation of one chat run, once, whoever gets there first: the
    generator, or the response when the client left before the generator ever ran."""

    def __init__(
        self, sessionmaker: SessionMaker, settings: Settings, reservation: uuid.UUID
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._reservation = reservation
        self.settled = False

    async def settle(self, ok: bool, usage: Usage, tool_calls: int) -> None:
        if self.settled:
            return
        self.settled = True
        await asyncio.shield(
            caps.finalize(
                self._sessionmaker,
                self._settings,
                self._reservation,
                usage=usage,
                ok=ok,
                tool_calls=tool_calls,
            )
        )


class ChatStreamResponse(StreamingResponse):
    """A streaming response that settles the run when it ends however it ends. The generator's
    own `finally` never runs if it was never started (the client left before the first event)."""

    def __init__(self, content: AsyncIterator[str], settler: Settler, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._settler = settler

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._settler.settle(False, Usage(model=""), 0)


def notify_shared(
    session: AsyncSession, thread: ChatThread, couple_id: uuid.UUID, me: uuid.UUID
) -> None:
    """Tell the other member the shared thread has a new message (sent once `session` commits)."""
    if thread.scope == "shared":
        payload = {"thread_id": str(thread.id)}
        emit(session, couple_id, "chat.message", payload, except_user=me)


async def stream_reply(
    *,
    ai: AIClient,
    providers: Providers,
    sessionmaker: SessionMaker,
    couple_id: uuid.UUID,
    me: uuid.UUID,
    thread_id: uuid.UUID,
    user_message: dict[str, Any],
    settler: Settler,
) -> AsyncIterator[str]:
    """The SSE body: `ack`, then `delta` and `status` events as the model works, then, once the
    reply is saved, one `action` event per proposal and `done`; or `error`. Caps are settled
    exactly once, before the last event, or when the client goes away (the generator is cancelled
    or closed). On a failure or a disconnect no assistant message is stored."""
    usage, tool_calls = Usage(model=""), 0

    try:
        yield sse("ack", {"message": user_message})
        trace: list[dict[str, Any]] = []
        finished: ChatFinished | None = None
        # The session stays open for the whole reply: the tools read through it. That pins a
        # connection for as long as the model takes, which is fine for a two-person household.
        async with sessionmaker() as session:
            thread = await session.get(ChatThread, thread_id)
            assert thread is not None  # checked before the stream began
            context, turns, tools = await _prepare(
                session, providers=providers, couple_id=couple_id, me=me, thread=thread
            )
            stream = ai.chat_stream(
                system=CHAT_SYSTEM, context=context, messages=turns, tools=tools
            )
            # Both clients return async generators; closing it stops the model call at once.
            async with aclosing(cast("AsyncGenerator[ChatEvent, None]", stream)) as events:
                async for event in events:
                    if isinstance(event, TextDelta):
                        yield sse("delta", {"text": event.text})
                    elif isinstance(event, ToolStarted):
                        trace.append({"name": event.name})
                        yield sse("status", {"label": status_label(event.name)})
                    elif isinstance(event, ChatFinished):
                        finished = event
        if finished is None:
            raise AIError("ai_unavailable", "The reply ended early.")
        usage, tool_calls = finished.usage, finished.tool_calls
        async with sessionmaker() as session, session.begin():
            thread = await session.get(ChatThread, thread_id)
            assert thread is not None
            reply = await service.add_message(
                session,
                thread,
                role="assistant",
                author=None,
                content=finished.text,
                actions=tools.actions,
                tool_trace=trace,
            )
            notify_shared(session, thread, couple_id, me)
            done = {
                "message_id": str(reply.id),
                "message": service.message_out(reply, me).model_dump(mode="json"),
            }
        await settler.settle(True, usage, tool_calls)
        # Only now, so a card is never shown before the message that holds it is saved.
        for action in tools.actions:
            yield sse("action", service.action_out(action).model_dump(mode="json"))
        yield sse("done", done)
    except AIError as exc:
        if exc.usage is not None:
            usage = exc.usage
        tool_calls = exc.tool_calls
        logger.warning("chat reply failed: %s", exc.code)
        await settler.settle(False, usage, tool_calls)
        yield sse("error", {"code": exc.code, "message": _FAILED})
    except Exception:
        logger.exception("chat reply failed")
        await settler.settle(False, usage, tool_calls)
        yield sse("error", {"code": "ai_failed", "message": _FAILED})
    finally:
        # The client went away mid-stream: close the books, store nothing.
        await settler.settle(False, usage, tool_calls)
