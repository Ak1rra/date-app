"""Realtime: the hub, the post-commit queue and the WebSocket endpoint.

The WebSocket tests drive the ASGI app directly on the test's own event loop (Starlette's
TestClient would run it on another loop, where the asyncpg engine can't follow).
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app import realtime
from app.realtime import emit, hub
from app.services.auth import SESSION_COOKIE_NAME
from tests.authutil import call
from tests.conftest import ORIGIN
from tests.itinerary_helpers import ITINERARIES, World, seed, when, world
from tests.test_ideas_api import IDEAS, shared_idea

TIMEOUT = 5.0


class FakeSocket:
    def __init__(self, *, broken: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.broken = broken

    async def send_json(self, message: dict[str, Any]) -> None:
        if self.broken:
            raise RuntimeError("closed")
        self.sent.append(message)


async def drain() -> None:
    """Let every scheduled publish finish."""
    while realtime._tasks:
        await asyncio.gather(*list(realtime._tasks))


def fake_session() -> tuple[Any, Session]:
    """What `emit` needs of an AsyncSession: a sync session, never connected to a database."""
    sync = Session()
    return SimpleNamespace(sync_session=sync), sync


# --- Hub and queue, without a database


async def test_publish_filters_by_couple_and_user() -> None:
    couple, other_couple = uuid.uuid4(), uuid.uuid4()
    me, partner, stranger = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sockets = {name: FakeSocket() for name in ("me", "partner", "stranger")}
    await hub.connect(couple, me, sockets["me"])  # type: ignore[arg-type]
    await hub.connect(couple, partner, sockets["partner"])  # type: ignore[arg-type]
    await hub.connect(other_couple, stranger, sockets["stranger"])  # type: ignore[arg-type]
    try:
        await hub.publish(couple, "a", {"n": 1})
        await hub.publish(couple, "b", {}, except_user=me)
        await hub.publish(couple, "c", {}, only_user=me)
    finally:
        await hub.disconnect(couple, me, sockets["me"])  # type: ignore[arg-type]
        await hub.disconnect(couple, partner, sockets["partner"])  # type: ignore[arg-type]
        await hub.disconnect(other_couple, stranger, sockets["stranger"])  # type: ignore[arg-type]
    assert sockets["me"].sent == [{"event": "a", "data": {"n": 1}}, {"event": "c", "data": {}}]
    assert sockets["partner"].sent == [{"event": "a", "data": {"n": 1}}, {"event": "b", "data": {}}]
    assert sockets["stranger"].sent == []


async def test_a_socket_whose_send_fails_is_dropped() -> None:
    couple, me, partner = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    broken, good = FakeSocket(broken=True), FakeSocket()
    await hub.connect(couple, me, broken)  # type: ignore[arg-type]
    await hub.connect(couple, partner, good)  # type: ignore[arg-type]
    try:
        await hub.publish(couple, "a", {})
        await hub.publish(couple, "b", {})
    finally:
        await hub.disconnect(couple, partner, good)  # type: ignore[arg-type]
    assert [m["event"] for m in good.sent] == ["a", "b"]
    assert (me, broken) not in hub._sockets.get(couple, set())


async def test_emit_publishes_after_commit_and_never_after_rollback() -> None:
    couple, me = uuid.uuid4(), uuid.uuid4()
    socket = FakeSocket()
    await hub.connect(couple, me, socket)  # type: ignore[arg-type]
    try:
        session, sync = fake_session()
        sync.begin()
        emit(session, couple, "lost", {})
        await drain()
        assert socket.sent == []  # nothing before the commit
        sync.rollback()
        sync.begin()
        sync.commit()  # the rolled-back event is gone, not merely delayed
        await drain()
        assert socket.sent == []

        sync.begin()
        emit(session, couple, "kept", {"id": "1"}, only_user=me)
        sync.commit()
        await drain()
        assert socket.sent == [{"event": "kept", "data": {"id": "1"}}]
        sync.begin()
        sync.commit()
        await drain()
        assert len(socket.sent) == 1  # the queue is emptied by the commit
    finally:
        await hub.disconnect(couple, me, socket)  # type: ignore[arg-type]


# --- The WebSocket, over ASGI


class Socket:
    """A WebSocket client for the ASGI app, on the running loop."""

    def __init__(
        self, app: FastAPI, *, origin: str | None = ORIGIN, as_: AsyncClient | None = None
    ):
        headers = [(b"host", b"testserver")]
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        if as_ is not None:
            cookie = "; ".join(f"{k}={v}" for k, v in as_.cookies.items())
            headers.append((b"cookie", cookie.encode()))
        self._app = app
        self._scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": "/api/v1/ws",
            "raw_path": b"/api/v1/ws",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "subprotocols": [],
        }
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def _receive(self) -> dict[str, Any]:
        return await self._inbox.get()

    async def _send(self, message: dict[str, Any]) -> None:
        await self._outbox.put(message)

    async def open(self) -> int | None:
        """None once the server accepts, else the code it closed the handshake with."""
        self._task = asyncio.create_task(self._app(self._scope, self._receive, self._send))
        await self._inbox.put({"type": "websocket.connect"})
        message = await asyncio.wait_for(self._outbox.get(), TIMEOUT)
        if message["type"] == "websocket.accept":
            return None
        assert message["type"] == "websocket.close"
        return int(message["code"])

    async def send(self, text: str) -> None:
        await self._inbox.put({"type": "websocket.receive", "text": text})

    async def text(self) -> str:
        message = await asyncio.wait_for(self._outbox.get(), TIMEOUT)
        assert message["type"] == "websocket.send"
        return str(message["text"])

    async def event(self) -> tuple[str, dict[str, Any]]:
        message = json.loads(await self.text())
        assert set(message) == {"event", "data"}
        return message["event"], message["data"]

    async def events(self, count: int) -> list[tuple[str, dict[str, Any]]]:
        return [await self.event() for _ in range(count)]

    async def assert_quiet(self) -> None:
        await drain()
        await asyncio.sleep(0.05)
        assert self._outbox.empty()

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            await self._inbox.put({"type": "websocket.disconnect", "code": 1000})
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(self._task, TIMEOUT)


@asynccontextmanager
async def connected(app: FastAPI, client: AsyncClient) -> AsyncIterator[Socket]:
    socket = Socket(app, as_=client)
    assert await socket.open() is None
    try:
        yield socket
    finally:
        await socket.close()


async def test_a_wrong_or_missing_origin_is_closed_with_4403(app: FastAPI) -> None:
    assert await Socket(app, origin="http://evil.example").open() == 4403
    assert await Socket(app, origin=None).open() == 4403


async def test_no_cookie_is_closed_with_4401(app: FastAPI) -> None:
    assert await Socket(app).open() == 4401


@pytest.mark.db
async def test_a_cookie_that_matches_no_session_is_closed_with_4401(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    db_client.cookies.set(SESSION_COOKIE_NAME, "not-a-real-token")
    assert await Socket(db_app, as_=db_client).open() == 4401


@pytest.mark.db
async def test_ping_gets_pong(db_app: FastAPI) -> None:
    async with world(db_app) as w, connected(db_app, w.owner) as socket:
        await socket.send("ping")
        assert await socket.text() == "pong"
        await socket.send("anything else")
        await socket.assert_quiet()
        assert w.couple_id in hub._sockets
    assert w.couple_id not in hub._sockets  # unregistered on disconnect


@pytest.mark.db
async def test_a_binary_frame_is_ignored(db_app: FastAPI) -> None:
    async with world(db_app) as w, connected(db_app, w.owner) as socket:
        await socket._inbox.put({"type": "websocket.receive", "bytes": b"\x00ping"})
        await socket.send("ping")
        assert await socket.text() == "pong"


# --- Events, end to end


@pytest.mark.db
async def test_a_partners_vote_reaches_me_and_not_the_voter(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        idea = await shared_idea(w.owner)
        async with connected(db_app, w.owner) as mine, connected(db_app, w.partner) as theirs:
            response = await call(
                w.partner, "PUT", f"{IDEAS}/{idea['id']}/vote", {"value": "maybe"}
            )
            assert response.status_code == 200, response.text
            assert await mine.event() == ("vote.updated", {"idea_id": idea["id"]})
            await theirs.assert_quiet()
            await mine.assert_quiet()

            undone = await call(w.partner, "POST", "/api/v1/deck/undo")
            assert undone.status_code == 200, undone.text
            assert await mine.event() == ("vote.updated", {"idea_id": idea["id"]})
            await theirs.assert_quiet()


@pytest.mark.db
async def test_sharing_an_idea_tells_the_partner_and_a_private_one_nothing(
    db_app: FastAPI,
) -> None:
    async with (
        world(db_app) as w,
        connected(db_app, w.owner) as mine,
        connected(db_app, w.partner) as theirs,
    ):
        created = await call(w.owner, "POST", IDEAS, {"title": "Picnic"})
        assert created.status_code == 201, created.text
        await theirs.assert_quiet()
        idea_id = created.json()["id"]
        shared = await call(w.owner, "POST", f"{IDEAS}/{idea_id}/share")
        assert shared.status_code == 200, shared.text
        assert await theirs.event() == ("idea.created", {"id": idea_id})
        await mine.assert_quiet()


@pytest.mark.db
async def test_a_match_reaches_both_members(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        idea = await shared_idea(w.owner, "Pottery")
        url = f"{IDEAS}/{idea['id']}/vote"
        async with connected(db_app, w.owner) as mine, connected(db_app, w.partner) as theirs:
            assert (await call(w.partner, "PUT", url, {"value": "yes"})).status_code == 200
            assert await mine.event() == ("vote.updated", {"idea_id": idea["id"]})
            assert (await call(w.owner, "PUT", url, {"value": "yes"})).status_code == 200
            match = ("match.created", {"idea_id": idea["id"], "title": "Pottery"})
            assert await mine.event() == match
            assert await theirs.events(2) == [("vote.updated", {"idea_id": idea["id"]}), match]
            await mine.assert_quiet()
            await theirs.assert_quiet()


async def schedule(w: World, itinerary_id: uuid.UUID, mode: str) -> Any:
    response = await call(
        w.owner,
        "POST",
        f"{ITINERARIES}/{itinerary_id}/schedule",
        {"starts_at": when(9).isoformat(), "mode": mode},
    )
    assert response.status_code == 200, response.text
    return response


@pytest.mark.db
async def test_an_announced_date_reaches_both_members(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w)
        async with connected(db_app, w.owner) as mine, connected(db_app, w.partner) as theirs:
            await schedule(w, itinerary_id, "announce")
            expected = ("date.scheduled", {"id": str(itinerary_id)})
            assert await mine.event() == expected
            assert await theirs.event() == expected

            cancelled = await call(w.owner, "POST", f"{ITINERARIES}/{itinerary_id}/cancel")
            assert cancelled.status_code == 200, cancelled.text
            expected = ("date.cancelled", {"id": str(itinerary_id)})
            assert await mine.event() == expected
            assert await theirs.event() == expected


@pytest.mark.db
async def test_an_unrevealed_surprise_tells_the_partner_only_that_the_calendar_changed(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w)
        async with connected(db_app, w.owner) as mine, connected(db_app, w.partner) as theirs:
            await schedule(w, itinerary_id, "surprise")
            assert await mine.event() == ("date.scheduled", {"id": str(itinerary_id)})
            assert await theirs.event() == ("calendar.changed", {})
            await mine.assert_quiet()
            await theirs.assert_quiet()


@pytest.mark.db
async def test_a_hidden_draft_edit_tells_the_partner_nothing(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w)
        async with connected(db_app, w.owner) as mine, connected(db_app, w.partner) as theirs:
            response = await call(
                w.owner, "PATCH", f"{ITINERARIES}/{itinerary_id}", {"title": "Renamed"}
            )
            assert response.status_code == 200, response.text
            assert await mine.event() == ("itinerary.updated", {"id": str(itinerary_id)})
            await theirs.assert_quiet()
