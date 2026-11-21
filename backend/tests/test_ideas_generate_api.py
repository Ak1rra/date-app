"""POST /ideas/generate end to end, with the fake AI and fake providers."""

import datetime as dt
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.ai.client import AIError, FakeAIClient, Usage, get_ai
from app.ai.schemas import GeneratedIdea, GeneratedIdeas, StopKind
from app.models import AIUsage, Idea
from tests.authutil import call
from tests.itinerary_helpers import DownPlaces, World, fake_providers, set_home, when, world

pytestmark = pytest.mark.db

GENERATE = "/api/v1/ideas/generate"
DECK = "/api/v1/deck"


def body(**changes: Any) -> dict[str, Any]:
    return {"mode": "match", **changes}


async def generate(w: World, **changes: Any) -> dict[str, Any]:
    response = await call(w.owner, "POST", GENERATE, body(**changes))
    assert response.status_code == 200, response.text
    out: dict[str, Any] = response.json()
    return out


async def stored(w: World) -> list[Idea]:
    async with w.app.state.sessionmaker() as session:
        return list((await session.execute(select(Idea).order_by(Idea.created_at))).scalars())


def idea(title: str, category: StopKind = "other", **fields: Any) -> GeneratedIdea:
    values: dict[str, Any] = {
        "title": title,
        "pitch": "A date.",
        "tags": [],
        "category": category,
        "evergreen": True,
    }
    values.update(fields)
    return GeneratedIdea(**values)


@asynccontextmanager
async def ai_returns(app: FastAPI, responder: Any) -> AsyncIterator[None]:
    app.dependency_overrides[get_ai] = lambda: FakeAIClient({GeneratedIdeas: responder})
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_ai, None)


def fixed(*ideas: GeneratedIdea) -> Any:
    async def respond(tools: Any, context: str, messages: Any) -> GeneratedIdeas:
        return GeneratedIdeas(ideas=list(ideas))

    return respond


# --- Match and plan


async def test_match_saves_shared_ideas_for_the_partners_deck(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        out = await generate(w)
        assert out["degraded"] == []
        ideas = out["ideas"]
        assert len(ideas) == 8
        assert {i["visibility"] for i in ideas} == {"shared"}
        assert {i["source"] for i in ideas} == {"ai"}
        assert all(i["created_by_me"] for i in ideas)
        assert len({i["category"] for i in ideas}) >= 3
        assert any(i["grounded"] for i in ideas) and not all(i["grounded"] for i in ideas)

        deck = (await w.partner.get(DECK)).json()["cards"]
        assert {c["id"] for c in deck} == {i["id"] for i in ideas}
        rows = await stored(w)
        assert {r.created_by_user_id for r in rows} == {w.owner_id}
        assert all(r.shared_at is not None and not r.is_surprise for r in rows)
        assert rows[0].generation["request"]["mode"] == "match"
        assert rows[0].currency == "USD"


async def test_plan_saves_private_ideas_only_i_can_see(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        out = await generate(w, mode="plan")
        assert {i["visibility"] for i in out["ideas"]} == {"private"}
        mine = (await w.owner.get("/api/v1/ideas")).json()
        assert {i["id"] for i in mine} == {i["id"] for i in out["ideas"]}
        assert (await w.partner.get("/api/v1/ideas")).json() == []
        assert (await w.partner.get(DECK)).json()["cards"] == []
        assert all(r.shared_at is None for r in await stored(w))


async def test_only_a_plan_request_can_make_a_surprise(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        await generate(w, mode="plan", surprise_me=True)
        rows = await stored(w)
        assert rows and all(r.is_surprise and r.visibility == "private" for r in rows)


async def test_a_shared_idea_is_never_a_surprise(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        await generate(w, mode="match", surprise_me=True)
        rows = await stored(w)
        assert rows and not any(r.is_surprise for r in rows)


# --- Grounding


async def test_unseen_ids_become_evergreen_and_ungrounded(db_app: FastAPI) -> None:
    async def respond(tools: Any, context: str, messages: Any) -> GeneratedIdeas:
        found = json.loads(await tools.execute("search_places", {"query": "dinner"}))["data"]
        start = when(days=5, hour=18)
        events = json.loads(
            await tools.execute(
                "search_events",
                {"start": start.isoformat(), "end": (start + dt.timedelta(days=1)).isoformat()},
            )
        )["data"]
        return GeneratedIdeas(
            ideas=[
                idea("Real place", "food", place_ids=[found[0]["place_id"]], evergreen=False),
                idea("Made-up place", "food", place_ids=["made-up"], evergreen=False),
                idea("Mixed", "activity", place_ids=["made-up", found[0]["place_id"]]),
                idea("Made-up event", "event", event_id="made-up", evergreen=False),
                idea("Real event", "event", event_id=events[0]["event_id"], evergreen=False),
            ]
        )

    async with world(db_app) as w:
        await set_home(w)
        async with ai_returns(db_app, respond):
            out = await generate(w, count=5)
        by_title = {i["title"]: i for i in out["ideas"]}
        assert by_title["Real place"]["grounded"] is True
        assert by_title["Real place"]["venue"] is not None
        assert by_title["Made-up place"]["grounded"] is False
        assert by_title["Made-up place"]["venue"] is None
        assert by_title["Made-up event"]["grounded"] is False
        assert by_title["Mixed"]["grounded"] is True
        rows = {r.title: r for r in await stored(w)}
        assert rows["Made-up place"].place_ids == [] and rows["Made-up event"].event_id is None
        assert rows["Mixed"].place_ids == [rows["Real place"].place_ids[0]]
        assert set(rows["Real place"].venue) == {"lat", "lng", "fetched_at"}
        real_event = rows["Real event"]
        assert real_event.grounded and real_event.event_id
        assert real_event.valid_until is not None
        assert real_event.generation["event"]["name"]
        assert by_title["Real event"]["event"]["name"] == real_event.generation["event"]["name"]


async def test_reports_down_providers(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        w.providers["p"] = fake_providers(places=DownPlaces())
        out = await generate(w)
        assert out["degraded"] == ["places"]
        assert out["ideas"] and not any(i["grounded"] and i["event"] is None for i in out["ideas"])


# --- Filtering


async def test_titles_of_active_ideas_are_dropped(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        mine = {"title": "COOK A NEW RECIPE together"}
        added = await call(w.owner, "POST", "/api/v1/ideas", mine)
        assert added.status_code == 201
        out = await generate(w, count=10)
        titles = [i["title"].casefold() for i in out["ideas"]]
        assert "cook a new recipe together" not in titles


async def test_titles_repeated_in_one_batch_are_dropped(db_app: FastAPI) -> None:
    async def twice(tools: Any, context: str, messages: Any) -> GeneratedIdeas:
        return GeneratedIdeas(ideas=[idea("Twin"), idea(" twin ", "food"), idea("Other", "drink")])

    async with world(db_app) as w:
        await set_home(w)
        async with ai_returns(db_app, twice):
            out = await generate(w)
        assert [i["title"] for i in out["ideas"]] == ["Twin", "Other"]


async def test_the_count_is_respected(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        assert len((await generate(w, count=5))["ideas"]) == 5
        assert len(await stored(w)) == 5


async def test_one_category_cannot_dominate(db_app: FastAPI) -> None:
    mixed = [*(idea(f"Food {n}", "food") for n in range(7)), idea("Walk", "outdoors")]
    mixed.append(idea("Game", "activity"))
    async with world(db_app) as w:
        await set_home(w)
        async with ai_returns(db_app, fixed(*mixed)):
            out = await generate(w, count=8)
        kinds = [i["category"] for i in out["ideas"]]
        assert kinds.count("food") == 4 and len(kinds) == 6
        assert [i["title"] for i in out["ideas"][:4]] == [f"Food {n}" for n in range(4)]


async def test_a_requested_category_is_not_capped(db_app: FastAPI) -> None:
    mixed = [*(idea(f"Food {n}", "food") for n in range(7)), idea("Walk", "outdoors")]
    async with world(db_app) as w:
        await set_home(w)
        async with ai_returns(db_app, fixed(*mixed)):
            out = await generate(w, count=8, category="food")
        assert [i["category"] for i in out["ideas"]].count("food") == 7


async def test_ideas_that_break_the_request_are_not_saved(db_app: FastAPI) -> None:
    returned = [
        idea("Wrong category", "outdoors"),
        idea("Wrong setting", "food", indoor_outdoor="outdoor"),
        idea("Too dear", "food", est_cost=100),
        idea("Fits", "food", indoor_outdoor="indoor", est_cost=15),
        idea("Either", "food", indoor_outdoor="either"),
        idea("Unset", "food"),
    ]
    async with world(db_app) as w:
        await set_home(w)
        async with ai_returns(db_app, fixed(*returned)):
            out = await generate(
                w, count=5, category="food", indoor_outdoor="indoor", budget_max=20
            )
        assert [i["title"] for i in out["ideas"]] == ["Fits", "Either", "Unset"]
        assert [r.title for r in await stored(w)] == ["Fits", "Either", "Unset"]


async def test_an_event_outside_the_window_is_dropped(db_app: FastAPI) -> None:
    runs = 0

    async def respond(tools: Any, context: str, messages: Any) -> GeneratedIdeas:
        start = when(days=5, hour=18)
        events = json.loads(
            await tools.execute(
                "search_events",
                {"start": start.isoformat(), "end": (start + dt.timedelta(days=1)).isoformat()},
            )
        )["data"]
        nonlocal runs
        runs += 1
        return GeneratedIdeas(
            ideas=[
                idea(
                    f"Real event {runs}", "event", event_id=events[0]["event_id"], evergreen=False
                ),
                idea(f"Evergreen {runs}", "activity"),
            ]
        )

    day = when(days=5, hour=18).date()
    async with world(db_app) as w:
        await set_home(w)
        async with ai_returns(db_app, respond):
            late = await generate(
                w, window_start=(day + dt.timedelta(days=3)).isoformat(), window_end="2099-01-01"
            )
            early = await generate(w, window_end=(day - dt.timedelta(days=3)).isoformat())
            inside = await generate(w, window_start=day.isoformat(), window_end=day.isoformat())
        assert [i["title"] for i in late["ideas"]] == ["Evergreen 1"]
        assert [i["title"] for i in early["ideas"]] == ["Evergreen 2"]
        assert [i["title"] for i in inside["ideas"]] == ["Real event 3", "Evergreen 3"]


async def test_the_returned_batch_is_not_dominated(db_app: FastAPI) -> None:
    mixed = [
        *(idea(f"Food {n}", "food") for n in range(4)),
        *(idea(f"Game {n}", "activity") for n in range(2)),
        *(idea(f"Walk {n}", "outdoors") for n in range(3)),
    ]
    async with world(db_app) as w:
        await set_home(w)
        async with ai_returns(db_app, fixed(*mixed)):
            out = await generate(w, count=5)
        assert [i["title"] for i in out["ideas"]] == [
            "Food 0",
            "Food 1",
            "Food 2",
            "Game 0",
            "Game 1",
        ]


async def test_nothing_fitting_is_a_502_and_saves_nothing(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        async with ai_returns(db_app, fixed(idea("Walk", "outdoors"))):
            response = await call(w.owner, "POST", GENERATE, body(category="food"))
        assert response.status_code == 502
        error = response.json()["error"]
        assert error["code"] == "ai_failed"
        assert "loosening the filters" in error["message"]
        assert await stored(w) == []


# --- Runs and caps


async def test_the_run_is_recorded_and_the_prompt_carries_the_request(db_app: FastAPI) -> None:
    seen: list[str] = []

    async def respond(tools: Any, context: str, messages: Any) -> GeneratedIdeas:
        seen.append(messages[-1]["content"])
        return GeneratedIdeas(ideas=[idea("Only one")])

    async with world(db_app) as w:
        await set_home(w)
        await call(w.owner, "POST", "/api/v1/ideas", {"title": "Already here"})
        async with ai_returns(db_app, respond):
            await generate(w, vibe="  rainy day  ", budget_max=60, indoor_outdoor="indoor")
        async with w.app.state.sessionmaker() as session:
            used = list((await session.execute(select(AIUsage))).scalars())
        assert [(u.feature, u.ok) for u in used] == [("ideas", True)]
        assert '"vibe": "rainy day"' in seen[0] and '"count": 8' in seen[0]
        assert '"avoid_titles": ["Already here"]' in seen[0]


async def test_the_daily_cap_returns_429(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        db_app.state.settings.ai_cap_ideas_per_day = 1
        await generate(w)
        response = await call(w.owner, "POST", GENERATE, body())
        assert response.status_code == 429, response.text
        assert response.json()["error"]["code"] == "ai_cap_reached"
        assert "reset_at" in response.json()["error"]


async def test_a_failing_run_is_recorded_and_saves_nothing(db_app: FastAPI) -> None:
    async def refuse(*args: Any) -> Any:
        raise AIError("refused", "no", usage=Usage(model="fake", input=40, output=7), tool_calls=2)

    async with world(db_app) as w:
        await set_home(w)
        async with ai_returns(db_app, refuse):
            response = await call(w.owner, "POST", GENERATE, body())
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "ai_failed"
        async with w.app.state.sessionmaker() as session:
            used = list((await session.execute(select(AIUsage))).scalars())
        assert [(u.feature, u.ok, u.tool_calls) for u in used] == [("ideas", False, 2)]
        assert await stored(w) == []


async def test_needs_a_home_area(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        response = await call(w.owner, "POST", GENERATE, body())
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "home_area_required"
        async with w.app.state.sessionmaker() as session:
            assert (await session.execute(select(AIUsage))).first() is None  # nothing reserved


async def test_ai_unavailable_is_503(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        db_app.state.settings.demo_mode = False  # no key and no demo mode: no AI client
        response = await call(w.owner, "POST", GENERATE, body())
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "ai_unavailable"


# --- Validation


async def test_request_validation(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        bad: list[dict[str, Any]] = [
            {"count": 4},
            {"count": 11},
            {"window_start": "2030-05-02", "window_end": "2030-05-01"},
            {"budget_max": 0},
            {"budget_max": 10_001},
            {"vibe": "x" * 201},
            {"category": "nope"},
            {"indoor_outdoor": "sideways"},
            {"surprise": True},  # unknown field
        ]
        for changes in bad:
            response = await call(w.owner, "POST", GENERATE, body(**changes))
            assert response.status_code == 422, changes
        missing_mode = await call(w.owner, "POST", GENERATE, {})
        assert missing_mode.status_code == 422
        same_day = await call(
            w.owner, "POST", GENERATE, body(window_start="2030-05-01", window_end="2030-05-01")
        )
        assert same_day.status_code == 200, same_day.text
        assert await stored(w)  # only the valid request saved anything
