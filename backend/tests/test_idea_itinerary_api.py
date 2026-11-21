"""POST /ideas/{id}/itinerary ("Make it a date"), with the fake AI and fake providers."""

import datetime as dt
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

import app.ai.fakes  # noqa: F401  (registers the fake responders)
from app.ai.client import FAKE_RESPONDERS, FakeAIClient, get_ai
from app.ai.schemas import ItineraryDraft
from app.models import AIUsage, Idea
from tests.authutil import call
from tests.itinerary_helpers import World, set_home, when, world
from tests.test_calendar_api import add_availability, set_timezone

pytestmark = pytest.mark.db

SUGGEST = "/api/v1/calendar/suggest-times"
PLAN = "/api/v1/plan/sessions"


def path(idea_id: uuid.UUID | str) -> str:
    return f"/api/v1/ideas/{idea_id}/itinerary"


async def add_idea(w: World, **fields: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "couple_id": w.couple_id,
        "created_by_user_id": w.owner_id,
        "source": "manual",
        "visibility": "shared",
        "title": "Rooftop picnic",
        "pitch": "Sunset and snacks.",
    }
    values.update(fields)
    async with w.app.state.sessionmaker() as session:
        idea = Idea(**values)
        session.add(idea)
        await session.commit()
        return idea.id


@contextmanager
def recording_ai(app: FastAPI) -> Iterator[list[str]]:
    """The fake AI, with the text of every build message it was sent."""
    seen: list[str] = []
    inner = FAKE_RESPONDERS[ItineraryDraft]

    async def record(tools: Any, context: str, messages: Any) -> Any:
        seen.append("\n".join(str(m["content"]) for m in messages))
        return await inner(tools, context, messages)

    app.dependency_overrides[get_ai] = lambda: FakeAIClient({ItineraryDraft: record})
    try:
        yield seen
    finally:
        app.dependency_overrides.pop(get_ai, None)


async def plan_for(w: World, itinerary_id: str) -> dict[str, Any]:
    plans = (await w.owner.get(PLAN)).json()
    (found,) = [p for p in plans if p["itinerary_id"] == itinerary_id]
    out: dict[str, Any] = found
    return out


async def usage(w: World) -> list[tuple[str, bool]]:
    async with w.app.state.sessionmaker() as session:
        rows = (await session.execute(select(AIUsage))).scalars()
        return [(u.feature, u.ok) for u in rows]


def same_instant(left: str, right: dt.datetime) -> bool:
    return dt.datetime.fromisoformat(left) == right


async def test_explicit_times_build_the_date_from_the_idea(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        idea_id = await add_idea(w)
        start = when(days=5, hour=18)
        response = await call(
            w.owner,
            "POST",
            path(idea_id),
            {"starts_at": start.isoformat(), "budget_max": 120},
        )
        assert response.status_code == 201, response.text
        out = response.json()
        assert out["idea_id"] == str(idea_id) and out["status"] == "draft"
        assert out["budget"] == 120 and out["is_surprise"] is False and out["stops"]
        plan = await plan_for(w, out["id"])
        assert same_instant(plan["inputs"]["window_start"], start)
        assert same_instant(plan["inputs"]["window_end"], start + dt.timedelta(hours=3))
        assert "Rooftop picnic" in plan["inputs"]["steer"]
        assert await usage(w) == [("build", True)]  # counted against the build cap


async def test_an_explicit_end_is_kept(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        idea_id = await add_idea(w)
        start = when(days=5, hour=12)
        end = start + dt.timedelta(hours=6)
        body = {"starts_at": start.isoformat(), "ends_at": end.isoformat()}
        response = await call(w.owner, "POST", path(idea_id), body)
        assert response.status_code == 201, response.text
        plan = await plan_for(w, response.json()["id"])
        assert same_instant(plan["inputs"]["window_end"], end)


async def test_the_event_name_goes_into_the_steer(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        generation = {"event": {"name": "Jazz in the Park", "venue": "Zilker Park"}}
        idea_id = await add_idea(w, generation=generation)
        start = when(days=5, hour=18)
        response = await call(w.owner, "POST", path(idea_id), {"starts_at": start.isoformat()})
        assert response.status_code == 201, response.text
        steer = (await plan_for(w, response.json()["id"]))["inputs"]["steer"]
        assert "Jazz in the Park" in steer and "Zilker Park" in steer


async def test_no_times_uses_the_first_suggested_slot(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        await set_timezone(w, "UTC")
        evenings = [(d, dt.time(17), dt.time(23)) for d in range(7)]
        await add_availability(w, "owner", windows=evenings)
        await add_availability(w, "partner", windows=evenings)
        idea_id = await add_idea(w)
        suggested = await call(
            w.owner, "POST", SUGGEST, {"duration_minutes": 180, "weeks": 3, "idea_id": str(idea_id)}
        )
        first = suggested.json()["slots"][0]
        response = await call(w.owner, "POST", path(idea_id), {})
        assert response.status_code == 201, response.text
        plan = await plan_for(w, response.json()["id"])
        assert same_instant(
            plan["inputs"]["window_start"], dt.datetime.fromisoformat(first["starts_at"])
        )
        assert same_instant(
            plan["inputs"]["window_end"], dt.datetime.fromisoformat(first["ends_at"])
        )


async def test_no_free_time_is_409(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        await set_timezone(w, "UTC")
        await add_availability(w, "owner", windows=[(d, dt.time(8), dt.time(9)) for d in range(7)])
        await add_availability(
            w, "partner", windows=[(d, dt.time(20), dt.time(21)) for d in range(7)]
        )
        idea_id = await add_idea(w)
        response = await call(w.owner, "POST", path(idea_id), {})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "no_free_time"
        assert await usage(w) == []


async def test_an_idea_you_cannot_see_is_404(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        private = await add_idea(w, visibility="private")  # the owner's own private idea
        body = {"starts_at": when(days=5).isoformat()}
        assert (await call(w.owner, "POST", path(private), body)).status_code == 201
        assert (await call(w.partner, "POST", path(private), body)).status_code == 404
        assert (await call(w.owner, "POST", path(uuid.uuid4()), body)).status_code == 404
        # The 404 wins over "the planner is unavailable".
        db_app.state.settings.demo_mode = False
        assert (await call(w.partner, "POST", path(private), body)).status_code == 404


async def test_stop_count_reaches_the_ai(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        idea_id = await add_idea(w)
        body = {"starts_at": when(days=5).isoformat(), "stop_count": 2}
        with recording_ai(db_app) as seen:
            response = await call(w.owner, "POST", path(idea_id), body)
        assert response.status_code == 201, response.text
        assert seen and "Use exactly 2 stops." in seen[0]
        plan = await plan_for(w, response.json()["id"])
        assert plan["inputs"]["stop_count"] == 2
        # Without a stop count the line is absent.
        with recording_ai(db_app) as seen:
            await call(w.owner, "POST", path(idea_id), {"starts_at": when(days=6).isoformat()})
        assert seen and "Use exactly" not in seen[0]


async def test_body_limits(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        idea_id = await add_idea(w)
        start = when(days=5).isoformat()
        for body in (
            {"starts_at": start, "stop_count": 0},
            {"starts_at": start, "stop_count": 6},
            {"starts_at": start, "budget_max": -1},
            {"starts_at": "2026-12-01T18:00:00"},  # not timezone-aware
        ):
            assert (await call(w.owner, "POST", path(idea_id), body)).status_code == 422, body


async def test_an_end_without_a_start_is_422(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        idea_id = await add_idea(w)
        response = await call(w.owner, "POST", path(idea_id), {"ends_at": when(days=5).isoformat()})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_window"


async def test_an_end_before_the_start_is_422(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        idea_id = await add_idea(w)
        start = when(days=5, hour=18)
        body = {
            "starts_at": start.isoformat(),
            "ends_at": (start - dt.timedelta(hours=1)).isoformat(),
        }
        response = await call(w.owner, "POST", path(idea_id), body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_window"


async def test_it_needs_a_home_area(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        idea_id = await add_idea(w)
        response = await call(
            w.owner, "POST", path(idea_id), {"starts_at": when(days=5).isoformat()}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "home_area_required"
