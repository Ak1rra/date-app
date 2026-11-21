"""Realtime events (m5 design, "Realtime"): an in-memory hub of the open WebSockets and a
post-commit queue the services emit into.

The app runs as one uvicorn process and the job worker lives inside it, so the hub reaches
every socket. A message is `{"event": str, "data": dict}`. Payloads carry ids only, never vote
values or surprise details: the client refetches over HTTP, which applies the visibility rules.

Services call `emit` (or `emit_itinerary`) after their writes. Nothing is sent until the
session commits, and nothing at all if it rolls back.
"""

import asyncio
import logging
import uuid
from typing import Any

from fastapi import WebSocket
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import Itinerary

logger = logging.getLogger(__name__)

_QUEUE_KEY = "realtime"


class Hub:
    """The open sockets, by couple. Sockets are never shared between couples."""

    def __init__(self) -> None:
        self._sockets: dict[uuid.UUID, set[tuple[uuid.UUID, WebSocket]]] = {}

    async def connect(self, couple_id: uuid.UUID, user_id: uuid.UUID, ws: WebSocket) -> None:
        self._sockets.setdefault(couple_id, set()).add((user_id, ws))

    async def disconnect(self, couple_id: uuid.UUID, user_id: uuid.UUID, ws: WebSocket) -> None:
        sockets = self._sockets.get(couple_id)
        if sockets is None:
            return
        sockets.discard((user_id, ws))
        if not sockets:
            del self._sockets[couple_id]

    async def publish(
        self,
        couple_id: uuid.UUID,
        event: str,
        payload: dict[str, Any],
        *,
        only_user: uuid.UUID | None = None,
        except_user: uuid.UUID | None = None,
    ) -> None:
        """Send to the couple's sockets: to `only_user`'s alone, or to everyone but
        `except_user`. A socket whose send fails is dropped."""
        targets = [
            (user_id, ws)
            for user_id, ws in self._sockets.get(couple_id, ())
            if (only_user is None or user_id == only_user)
            and (except_user is None or user_id != except_user)
        ]
        if not targets:
            return
        message = {"event": event, "data": payload}
        results = await asyncio.gather(
            *(ws.send_json(message) for _, ws in targets), return_exceptions=True
        )
        for (user_id, ws), result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                await self.disconnect(couple_id, user_id, ws)


hub = Hub()

# Strong references, so a scheduled publish isn't garbage-collected before it runs.
_tasks: set[asyncio.Task[None]] = set()


def emit(
    session: AsyncSession,
    couple_id: uuid.UUID,
    event: str,
    payload: dict[str, Any],
    *,
    only_user: uuid.UUID | None = None,
    except_user: uuid.UUID | None = None,
) -> None:
    """Queue an event for the couple; it is published once `session` commits."""
    queue: list[tuple[Any, ...]] = session.sync_session.info.setdefault(_QUEUE_KEY, [])
    queue.append((couple_id, event, payload, only_user, except_user))


@sa_event.listens_for(Session, "after_commit")
def _publish_queued(session: Session) -> None:
    queue = session.info.pop(_QUEUE_KEY, None)
    if not queue:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # committed outside an event loop: nobody is listening
        return
    for couple_id, event, payload, only_user, except_user in queue:
        task = loop.create_task(
            hub.publish(couple_id, event, payload, only_user=only_user, except_user=except_user)
        )
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


@sa_event.listens_for(Session, "after_rollback")
def _discard_queued(session: Session) -> None:
    session.info.pop(_QUEUE_KEY, None)


def emit_itinerary(
    session: AsyncSession,
    itinerary: Itinerary,
    event: str,
    *,
    notify_partner: bool = True,
) -> None:
    """Tell the couple an itinerary changed, as each member may know of it.

    The planner always gets `event {id}`. The other member gets it only if the itinerary is
    visible to them (the rule in `itinerary_access`: announced and not a surprise, or a
    revealed surprise), judged on the in-memory object. For a surprise they cannot see yet
    (scheduled, or just cancelled or changed) they get an id-less `calendar.changed` so
    their calendar refreshes without learning anything. A hidden draft sends them nothing.
    `notify_partner=False` keeps a change that only the planner can see to the planner."""
    couple_id, planner = itinerary.couple_id, itinerary.planner_user_id
    payload = {"id": str(itinerary.id)}
    emit(session, couple_id, event, payload, only_user=planner)
    if not notify_partner:
        return
    visible = (itinerary.announced_at is not None and not itinerary.is_surprise) or (
        itinerary.is_surprise and itinerary.revealed_at is not None
    )
    if visible:
        emit(session, couple_id, event, payload, except_user=planner)
    elif itinerary.is_surprise and itinerary.status != "draft":
        emit(session, couple_id, "calendar.changed", {}, except_user=planner)
