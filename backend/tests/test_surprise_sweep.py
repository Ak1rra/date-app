"""Every GET route must hide an unrevealed surprise from the surprisee."""

import datetime as dt
import uuid
from collections.abc import Iterable

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response

from app.models import (
    ChatMessage,
    ChecklistItem,
    Idea,
    Itinerary,
    PlannerNote,
    PlanSession,
    Stop,
    SurpriseHint,
)
from tests.authutil import profile_ids
from tests.itinerary_helpers import World, when, world
from tests.test_authz_sweep import API, ROUTES, _param_names

pytestmark = pytest.mark.db


MARKERS = (
    "MARKER-TITLE-7f3a",
    "MARKER-STOP-TITLE-1c9e",
    "MARKER-VENUE-NAME-4b2d",
    "MARKER-STOP-NOTES-0e63",
    "MARKER-INVITATION-8a6f",
    "MARKER-PLANNER-NOTE-3e71",
    "MARKER-PRIVATE-IDEA-5d84",
    "MARKER-PRIVATE-PITCH-a812",
    "MARKER-PRIVATE-CHAT-9b20",
    "MARKER-HIDDEN-HINT-6c48",
    "MARKER-CHECKLIST-2a97",
)


def _get_routes() -> list[tuple[str, str]]:
    """GET routes in the authz table, excluding the WebSocket route."""
    return [(method, path) for method, path in ROUTES if method == "GET"]


def _path(template: str, ids: dict[str, str]) -> str:
    missing = [name for name in _param_names(template) if name not in ids]
    assert not missing, f"No seeded id for GET route {template}: {missing}"
    return API + template.format(**ids)


async def _get_sweep(client: AsyncClient, ids: dict[str, str]) -> list[tuple[str, Response]]:
    responses: list[tuple[str, Response]] = []
    for _, template in _get_routes():
        response = await client.get(_path(template, ids))
        responses.append((template, response))
    return responses


def _marker_text(responses: Iterable[tuple[str, Response]]) -> str:
    return "\n".join(response.text for _, response in responses).lower()


async def _seed_marked_surprise(w: World) -> dict[str, str]:
    """Seed the planner's private data and an unrevealed surprise, returning route ids."""
    profile_id = (await profile_ids(w.owner))["partner"]
    planner_threads = (await w.owner.get(f"{API}/chat/threads")).json()
    private_thread_id = next(t["id"] for t in planner_threads if t["scope"] == "private")
    plan_id = uuid.uuid4()
    starts_at = when()

    async with w.app.state.sessionmaker() as session:
        itinerary = Itinerary(
            couple_id=w.couple_id,
            planner_user_id=w.owner_id,
            title=MARKERS[0],
            status="scheduled",
            starts_at=starts_at,
            ends_at=starts_at + dt.timedelta(hours=3),
            invitation_message=MARKERS[4],
            is_surprise=True,
            reveal_at=when(20),
        )
        session.add(itinerary)
        await session.flush()
        stop = Stop(
            itinerary_id=itinerary.id,
            position=0,
            title=MARKERS[1],
            venue={"name": MARKERS[2]},
            notes=MARKERS[3],
            starts_at=starts_at,
            ends_at=starts_at + dt.timedelta(hours=1),
        )
        session.add_all(
            [
                stop,
                ChecklistItem(
                    itinerary_id=itinerary.id,
                    position=0,
                    text=MARKERS[10],
                    kind="other",
                ),
                SurpriseHint(
                    itinerary_id=itinerary.id,
                    kind="custom",
                    text=MARKERS[9],
                    release_at=when(20),
                ),
                PlannerNote(
                    couple_id=w.couple_id,
                    author_user_id=w.owner_id,
                    about_profile_id=uuid.UUID(profile_id),
                    kind="note",
                    text=MARKERS[5],
                ),
                Idea(
                    couple_id=w.couple_id,
                    created_by_user_id=w.owner_id,
                    source="manual",
                    visibility="private",
                    title=MARKERS[6],
                    pitch=MARKERS[7],
                    is_surprise=True,
                ),
                ChatMessage(
                    thread_id=uuid.UUID(private_thread_id),
                    role="assistant",
                    author_user_id=w.owner_id,
                    content=MARKERS[8],
                ),
                PlanSession(
                    id=plan_id,
                    couple_id=w.couple_id,
                    planner_user_id=w.owner_id,
                    recipient_profile_id=uuid.UUID(profile_id),
                    inputs={"title": MARKERS[0]},
                ),
            ]
        )
        await session.commit()

    return {
        "itinerary_id": str(itinerary.id),
        "plan_id": str(plan_id),
        "profile_id": profile_id,
        "thread_id": private_thread_id,
    }


async def test_an_unrevealed_surprise_marker_is_hidden_from_every_get_route(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        ids = await _seed_marked_surprise(w)
        planner_responses = await _get_sweep(w.owner, ids)
        planner_body = _marker_text(planner_responses)
        assert any(marker.lower() in planner_body for marker in MARKERS)

        surprisee_responses = await _get_sweep(w.partner, ids)
        for template, response in surprisee_responses:
            assert response.status_code < 500, (
                f"GET {template}: {response.status_code}: {response.text}"
            )
            body = response.text.lower()
            assert not any(marker.lower() in body for marker in MARKERS), (
                f"GET {template} leaked a surprise marker: {response.text}"
            )
