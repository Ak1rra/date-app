"""POST /chat/threads/{id}/messages: the SSE stream, with the fake AI and fake providers."""

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager, suppress
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy import select

import app.ai.fakes  # noqa: F401  (registers the fake responders)
from app.ai import caps
from app.ai.client import (
    AIError,
    ChatEvent,
    ChatFinished,
    ChatResponder,
    FakeAIClient,
    TextDelta,
    ToolFinished,
    ToolRunner,
    ToolStarted,
    Usage,
    get_ai,
)
from app.models import AIUsage, ChatMessage, Idea, PartnerProfile, PlannerNote
from app.services.chat_stream import ChatStreamResponse, Settler
from tests.authutil import call
from tests.itinerary_helpers import World, world
from tests.test_chat_api import add_message, messages_path, threads_of

pytestmark = pytest.mark.db


@contextmanager
def fake_ai(app: FastAPI, responder: ChatResponder | None = None) -> Iterator[None]:
    app.dependency_overrides[get_ai] = lambda: FakeAIClient(chat_responder=responder)
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_ai, None)


def parse(response: Response) -> list[tuple[str, Any]]:
    """The SSE body as (event, data) pairs."""
    events: list[tuple[str, Any]] = []
    for block in response.text.split("\n\n"):
        if not block.strip():
            continue
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events


async def send(client: AsyncClient, thread: str, content: str = "hi there") -> Response:
    return await call(client, "POST", messages_path(thread), {"content": content})


async def stored(w: World, thread: str) -> list[ChatMessage]:
    async with w.app.state.sessionmaker() as session:
        query = (
            select(ChatMessage)
            .where(ChatMessage.thread_id == uuid.UUID(thread))
            .order_by(ChatMessage.created_at, ChatMessage.id)
        )
        return list((await session.execute(query)).scalars())


def script(*events: ChatEvent, text: str = "Sounds lovely.") -> ChatResponder:
    async def responder(
        tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
    ) -> AsyncIterator[ChatEvent]:
        for event in events:
            yield event
        yield ChatFinished(text=text, usage=Usage(model="fake"), tool_calls=0, degraded=set())

    return responder


# --- The stream ---------------------------------------------------------------------------


async def test_a_reply_streams_ack_deltas_and_done_and_stores_both_messages(
    db_app: FastAPI,
) -> None:
    with fake_ai(db_app):
        async with world(db_app) as w:
            thread = (await threads_of(w.owner))["private"]
            response = await send(w.owner, thread, "  Plan something cosy  ")
            assert response.status_code == 200, response.text
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-cache"
            assert response.headers["x-accel-buffering"] == "no"

            events = parse(response)
            names = [name for name, _ in events]
            assert names[0] == "ack" and names[-1] == "done"
            assert set(names[1:-1]) == {"delta"} and len(names) > 2
            ack, done = events[0][1]["message"], events[-1][1]
            assert ack["role"] == "user" and ack["author_is_me"] is True
            assert ack["content"] == "Plan something cosy"
            text = "".join(data["text"] for name, data in events if name == "delta")
            assert done["message"]["content"] == text
            assert done["message_id"] == done["message"]["id"]
            assert done["message"]["role"] == "assistant"
            assert done["message"]["author_user_id"] is None

            user, reply = await stored(w, thread)
            assert (user.role, user.author_user_id, str(user.id)) == ("user", w.owner_id, ack["id"])
            assert (reply.role, reply.author_user_id, str(reply.id)) == (
                "assistant",
                None,
                done["message_id"],
            )
            assert reply.content == text and reply.actions == []
            history = (await w.owner.get(messages_path(thread))).json()["messages"]
            assert [m["role"] for m in history] == ["user", "assistant"]
            async with w.app.state.sessionmaker() as session:
                (usage,) = (await session.execute(select(AIUsage))).scalars()
                assert usage.feature == "chat" and usage.ok is True


async def test_a_tool_call_sends_a_status_label(db_app: FastAPI) -> None:
    responder = script(
        ToolStarted("search_places"),
        ToolFinished("search_places"),
        ToolStarted("get_weather"),
        ToolFinished("get_weather"),
        ToolStarted("something_new"),
        ToolFinished("something_new"),
        TextDelta("Here you go."),
    )
    with fake_ai(db_app, responder):
        async with world(db_app) as w:
            thread = (await threads_of(w.owner))["private"]
            events = parse(await send(w.owner, thread))
            assert [d["label"] for n, d in events if n == "status"] == [
                "Finding places…",
                "Checking the weather…",
                "Thinking…",
            ]
            assert [n for n, _ in events] == ["ack", "status", "status", "status", "delta", "done"]
            (_, reply) = await stored(w, thread)
            assert [t["name"] for t in reply.tool_trace] == [
                "search_places",
                "get_weather",
                "something_new",
            ]


async def test_a_proposal_streams_an_action_and_is_stored_unapplied(db_app: FastAPI) -> None:
    with fake_ai(db_app):  # the fake proposes an idea when the message mentions one
        async with world(db_app) as w:
            thread = (await threads_of(w.owner))["private"]
            events = parse(await send(w.owner, thread, "Any idea for Saturday?"))
            names = [n for n, _ in events]
            assert names[:2] == ["ack", "status"] and names[-2:] == ["action", "done"]
            assert names.count("action") == 1  # held back until the reply is saved
            assert events[1][1] == {"label": "Drafting a suggestion…"}
            action = events[-2][1]
            assert action["type"] == "idea" and action["applied_at"] is None
            assert action["payload"]["title"] == "Sunset picnic"
            reply = events[-1][1]["message"]
            assert [a["id"] for a in reply["actions"]] == [action["id"]]
            (_, row) = await stored(w, thread)
            assert [a["id"] for a in row.actions] == [action["id"]]
            assert row.actions[0]["applied_at"] is None
            async with w.app.state.sessionmaker() as session:
                assert (await session.execute(select(Idea))).first() is None  # nothing saved


# --- Before the stream --------------------------------------------------------------------


async def test_the_daily_cap_is_a_json_429_and_stores_nothing(db_app: FastAPI) -> None:
    with fake_ai(db_app):
        async with world(db_app) as w:
            db_app.state.settings.ai_cap_chat_per_day = 1
            thread = (await threads_of(w.owner))["private"]
            assert (await send(w.owner, thread)).status_code == 200
            response = await send(w.owner, thread, "one more")
            assert response.status_code == 429, response.text
            assert response.headers["content-type"].startswith("application/json")
            assert response.json()["error"]["code"] == "ai_cap_reached"
            assert [m.content for m in await stored(w, thread) if m.role == "user"] == ["hi there"]


async def test_the_partners_private_thread_is_a_404(db_app: FastAPI) -> None:
    with fake_ai(db_app):
        async with world(db_app) as w:
            theirs = (await threads_of(w.partner))["private"]
            response = await send(w.owner, theirs)
            assert response.status_code == 404
            assert response.headers["content-type"].startswith("application/json")
            assert await stored(w, theirs) == []
            async with w.app.state.sessionmaker() as session:
                assert (await session.execute(select(AIUsage))).first() is None  # no run reserved


async def test_bad_input_auth_and_csrf_are_plain_errors(db_app: FastAPI) -> None:
    with fake_ai(db_app):
        async with world(db_app) as w:
            thread = (await threads_of(w.owner))["private"]
            for content in ("", "   ", "x" * 2001):
                assert (await send(w.owner, thread, content)).status_code == 422, content
            assert (await call(w.owner, "POST", messages_path(thread), {})).status_code == 422
            bare = await w.owner.post(messages_path(thread), json={"content": "hi"})
            assert bare.status_code == 403
            assert await stored(w, thread) == []
            w.owner.cookies.clear()
            assert (await send(w.owner, thread)).status_code == 401


# --- Failures -----------------------------------------------------------------------------


async def test_an_ai_error_ends_with_an_error_event_and_stores_no_reply(db_app: FastAPI) -> None:
    async def failing(
        tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
    ) -> AsyncIterator[ChatEvent]:
        yield TextDelta("Let me thi")
        raise AIError("ai_unavailable", "down", usage=Usage(model="fake", input=9, output=3))

    with fake_ai(db_app, failing):
        async with world(db_app) as w:
            thread = (await threads_of(w.owner))["private"]
            response = await send(w.owner, thread)
            assert response.status_code == 200
            events = parse(response)
            assert [n for n, _ in events] == ["ack", "delta", "error"]
            assert events[-1][1]["code"] == "ai_unavailable" and events[-1][1]["message"]
            (only,) = await stored(w, thread)
            assert only.role == "user"
            async with w.app.state.sessionmaker() as session:
                (usage,) = (await session.execute(select(AIUsage))).scalars()
                assert (usage.ok, usage.input_tokens, usage.output_tokens) == (False, 9, 3)


async def test_the_error_code_is_the_ai_errors(db_app: FastAPI) -> None:
    async def refusing(
        tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
    ) -> AsyncIterator[ChatEvent]:
        raise AIError("refused", "no")
        yield  # pragma: no cover - makes this an async generator

    with fake_ai(db_app, refusing):
        async with world(db_app) as w:
            thread = (await threads_of(w.owner))["private"]
            events = parse(await send(w.owner, thread))
            assert events[-1][0] == "error" and events[-1][1]["code"] == "refused"


async def test_a_stream_that_never_finishes_is_an_error_and_stores_no_reply(
    db_app: FastAPI,
) -> None:
    async def empty(
        tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
    ) -> AsyncIterator[ChatEvent]:
        return  # the model never finishes
        yield  # pragma: no cover - makes this an async generator

    with fake_ai(db_app, empty):
        async with world(db_app) as w:
            thread = (await threads_of(w.owner))["private"]
            events = parse(await send(w.owner, thread))
            assert events[-1][0] == "error" and events[-1][1]["code"] == "ai_unavailable"
            assert [m.role for m in await stored(w, thread)] == ["user"]


# --- What the model is given --------------------------------------------------------------


async def test_history_is_the_visible_turns_and_the_shared_thread_names_its_authors(
    db_app: FastAPI,
) -> None:
    seen: dict[str, Any] = {}

    async def spy(
        tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
    ) -> AsyncIterator[ChatEvent]:
        seen["messages"] = messages
        yield ChatFinished(text="ok", usage=Usage(model="fake"), tool_calls=0, degraded=set())

    with fake_ai(db_app, spy):
        async with world(db_app) as w:
            async with w.app.state.sessionmaker() as session:
                for profile in (await session.execute(select(PartnerProfile))).scalars():
                    profile.display_name = "Ann Smith" if profile.slot == "owner" else "Bea Jones"
                await session.commit()
            threads = await threads_of(w.owner)
            shared = threads["shared"]
            await add_message(w, shared, "Ann's first", author=w.owner_id)
            await add_message(w, shared, "Bea chimes in", author=w.partner_id)
            await add_message(w, shared, "A reply", role="assistant")
            await add_message(w, shared, "Hidden", author=w.partner_id, hidden_for=w.owner_id)
            await add_message(
                w, shared, "Secret from Bea", author=w.owner_id, hidden_for=w.partner_id
            )
            await send(w.owner, shared, "Ann again")
            turns = seen["messages"]
            assert [t["role"] for t in turns] == ["user", "assistant", "user"]
            first, second = turns[0]["content"].split("\n\n")
            assert (first, second) == ("Ann: Ann's first", "Bea: Bea chimes in")
            assert turns[1]["content"] == "A reply"
            assert turns[2]["content"] == "Ann: Ann again"
            assert "Hidden" not in json.dumps(turns)
            assert "Secret from Bea" not in json.dumps(turns)  # hidden from the partner

            await send(w.owner, threads["private"], "Just me")
            assert seen["messages"] == [{"role": "user", "content": "Just me"}]


async def test_a_shared_reply_never_sees_notes_or_surprises(db_app: FastAPI) -> None:
    seen: dict[str, str] = {}

    async def spy(
        tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
    ) -> AsyncIterator[ChatEvent]:
        assert tools is not None
        seen["context"] = context
        seen["tool"] = await tools.execute("get_couple_context", {})
        yield ChatFinished(text="ok", usage=Usage(model="fake"), tool_calls=0, degraded=set())

    with fake_ai(db_app, spy):
        async with world(db_app) as w:
            async with w.app.state.sessionmaker() as session:
                profile_id = (
                    await session.execute(
                        select(PartnerProfile.id).where(
                            PartnerProfile.couple_id == w.couple_id,
                            PartnerProfile.slot == "partner",
                        )
                    )
                ).scalar_one()
                session.add(
                    PlannerNote(
                        couple_id=w.couple_id,
                        author_user_id=w.owner_id,
                        about_profile_id=profile_id,
                        kind="note",
                        text="SECRET-NOTE",
                    )
                )
                session.add(
                    Idea(
                        couple_id=w.couple_id,
                        created_by_user_id=w.owner_id,
                        source="manual",
                        visibility="private",
                        title="SECRET-SURPRISE",
                        pitch="p",
                        is_surprise=True,
                    )
                )
                await session.commit()
            threads = await threads_of(w.owner)

            await send(w.owner, threads["private"])
            assert "SECRET-NOTE" in seen["context"] and "SECRET-SURPRISE" in seen["context"]
            assert "SECRET-NOTE" in seen["tool"]

            await send(w.owner, threads["shared"])
            for text in (seen["context"], seen["tool"]):
                assert "SECRET-NOTE" not in text and "SECRET-SURPRISE" not in text


# --- Realtime -----------------------------------------------------------------------------


def recorder(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    emitted: list[tuple[Any, ...]] = []

    def record(session: Any, couple_id: Any, event: str, payload: Any, **kw: Any) -> None:
        emitted.append((couple_id, event, payload, kw))

    monkeypatch.setattr("app.services.chat_stream.emit", record)
    return emitted


async def test_chat_message_is_emitted_for_the_shared_thread_only(
    db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    emitted = recorder(monkeypatch)
    with fake_ai(db_app):
        async with world(db_app) as w:
            threads = await threads_of(w.owner)
            await send(w.owner, threads["private"])
            assert emitted == []
            await send(w.owner, threads["shared"])
            one = (
                w.couple_id,
                "chat.message",
                {"thread_id": threads["shared"]},
                {"except_user": w.owner_id},
            )
            assert emitted == [one, one]  # the message, then the reply
            # Both members see the reply in the shared thread.
            theirs = (await w.partner.get(messages_path(threads["shared"]))).json()["messages"]
            assert [m["role"] for m in theirs] == ["user", "assistant"]


async def test_the_partner_hears_of_a_shared_message_even_when_the_reply_fails(
    db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(
        tools: ToolRunner | None, context: str, messages: list[dict[str, Any]]
    ) -> AsyncIterator[ChatEvent]:
        raise AIError("ai_unavailable", "down")
        yield  # pragma: no cover - makes this an async generator

    emitted = recorder(monkeypatch)
    with fake_ai(db_app, failing):
        async with world(db_app) as w:
            shared = (await threads_of(w.owner))["shared"]
            events = parse(await send(w.owner, shared))
            assert events[-1][0] == "error"
            assert len(emitted) == 1 and emitted[0][1] == "chat.message"


# --- The client leaves before the stream starts ---------------------------------------------


async def test_a_response_that_never_started_still_settles_the_caps(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        sessionmaker = db_app.state.sessionmaker
        settings = db_app.state.settings
        reservation = await caps.reserve(
            sessionmaker, settings, couple_id=w.couple_id, user_id=w.owner_id, feature="chat"
        )
        settler = Settler(sessionmaker, settings, reservation)

        async def never_iterated() -> AsyncIterator[str]:
            yield "event: ack\ndata: {}\n\n"

        async def receive() -> dict[str, Any]:
            return {"type": "http.disconnect"}

        async def send_fails(message: dict[str, Any]) -> None:
            raise OSError("the client is gone")

        response = ChatStreamResponse(never_iterated(), settler, media_type="text/event-stream")
        scope = {"type": "http", "asgi": {"spec_version": "2.4"}}
        with suppress(Exception):  # what the server raises for a dead client is its business
            await response(scope, receive, send_fails)
        assert settler.settled
        async with sessionmaker() as session:
            (usage,) = (await session.execute(select(AIUsage))).scalars()
            assert (usage.ok, usage.model) == (False, "")  # no longer a pending placeholder
