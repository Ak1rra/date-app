"""Templates (FR-PLAN-8): save an itinerary as one, list, delete and re-plan it."""

import datetime as dt
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.models import AIUsage, PlanTemplate
from tests.authutil import call
from tests.itinerary_helpers import World, seed, set_home, when, world
from tests.test_idea_itinerary_api import recording_ai, usage

pytestmark = pytest.mark.db

ITINERARIES = "/api/v1/itineraries"
TEMPLATES = "/api/v1/templates"

STOPS: list[dict[str, Any]] = [
    {
        "title": "Tacos",
        "place_id": "p1",
        "description": "Street tacos",
        "est_cost": 20,
        "notes": "Order the al pastor",
        "starts_at": when(days=3, hour=18),
        "ends_at": when(days=3, hour=19),
    },
    {"title": "Stargazing", "starts_at": when(days=3, hour=20), "ends_at": when(days=3, hour=22)},
]


async def seeded(w: World, **fields: Any) -> str:
    return str(await seed(w, stops=STOPS, **fields))


async def save(client: Any, itinerary_id: str, name: str = "Taco night") -> Any:
    return await call(client, "POST", f"{ITINERARIES}/{itinerary_id}/template", {"name": name})


async def count_templates(w: World) -> int:
    async with w.app.state.sessionmaker() as session:
        return (await session.execute(select(func.count()).select_from(PlanTemplate))).scalar_one()


async def test_save_snapshots_the_stops_without_times(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seeded(w, budget=150, backup_plan="Go inside", bring_wear="Jacket")
        response = await save(w.owner, itinerary_id, "  Taco night  ")
        assert response.status_code == 201, response.text
        out = response.json()
        assert out["name"] == "Taco night" and out["title"] == "Date night"
        assert out["stop_titles"] == ["Tacos", "Stargazing"]
        assert out["source_itinerary_id"] == itinerary_id and "created_at" in out
        async with w.app.state.sessionmaker() as session:
            template = (await session.execute(select(PlanTemplate))).scalar_one()
        assert template.planner_user_id == w.owner_id and template.couple_id == w.couple_id
        assert template.snapshot == {
            "title": "Date night",
            "budget": 150.0,
            "backup_plan": "Go inside",
            "bring_wear": "Jacket",
            "stops": [
                {
                    "title": "Tacos",
                    "place_id": "p1",
                    "description": "Street tacos",
                    "est_cost": 20.0,
                    "notes": "Order the al pastor",
                },
                {
                    "title": "Stargazing",
                    "place_id": None,
                    "description": None,
                    "est_cost": None,
                    "notes": None,
                },
            ],
        }


async def test_save_validates_the_name(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seeded(w)
        for name in ("", "   ", "x" * 121):
            assert (await save(w.owner, itinerary_id, name)).status_code == 422, name
        assert (await save(w.owner, itinerary_id, "x" * 120)).status_code == 201


async def test_only_the_planner_can_save(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        shared = await seeded(
            w, status="scheduled", announced_at=dt.datetime.now(dt.UTC), is_surprise=False
        )
        response = await save(w.partner, shared)
        assert response.status_code == 403  # visible to them, but not theirs to change
        hidden = await seeded(w)  # an unannounced draft
        assert (await save(w.partner, hidden)).status_code == 404
        assert (await save(w.owner, str(uuid.uuid4()))).status_code == 404
        assert await count_templates(w) == 0


async def test_list_shows_only_your_own_newest_first(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        mine = await seeded(w)
        theirs = str(await seed(w, planner="partner", stops=STOPS))
        assert (await save(w.owner, mine, "First")).status_code == 201
        assert (await save(w.owner, mine, "Second")).status_code == 201
        assert (await save(w.partner, theirs, "Partner's")).status_code == 201
        names = [t["name"] for t in (await w.owner.get(TEMPLATES)).json()]
        assert names == ["Second", "First"]
        assert [t["name"] for t in (await w.partner.get(TEMPLATES)).json()] == ["Partner's"]


async def test_delete_is_only_for_your_own(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        template_id = (await save(w.owner, await seeded(w))).json()["id"]
        assert (await call(w.partner, "DELETE", f"{TEMPLATES}/{template_id}")).status_code == 404
        assert await count_templates(w) == 1
        assert (await call(w.owner, "DELETE", f"{TEMPLATES}/{uuid.uuid4()}")).status_code == 404
        assert (await call(w.owner, "DELETE", f"{TEMPLATES}/{template_id}")).status_code == 204
        assert await count_templates(w) == 0
        assert (await w.owner.get(TEMPLATES)).json() == []


async def test_the_51st_template_is_409(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seeded(w)
        async with w.app.state.sessionmaker() as session:
            session.add_all(
                PlanTemplate(
                    couple_id=w.couple_id,
                    planner_user_id=w.owner_id,
                    name=f"T{i}",
                    snapshot={"title": "x", "stops": []},
                )
                for i in range(50)
            )
            await session.commit()
        response = await save(w.owner, itinerary_id)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "template_limit"
        assert await count_templates(w) == 50
        # The partner has their own allowance.
        theirs = str(await seed(w, planner="partner", stops=STOPS))
        assert (await save(w.partner, theirs)).status_code == 201


async def test_replan_builds_a_new_date_as_a_replan_run(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        template_id = (await save(w.owner, await seeded(w, budget=150))).json()["id"]
        start = when(days=7, hour=18)
        with recording_ai(db_app) as seen:
            response = await call(
                w.owner,
                "POST",
                f"{TEMPLATES}/{template_id}/replan",
                {"starts_at": start.isoformat()},
            )
        assert response.status_code == 201, response.text
        out = response.json()
        assert out["status"] == "draft" and out["stops"] and out["idea_id"] is None
        assert out["id"] != (await w.owner.get(TEMPLATES)).json()[0]["source_itinerary_id"]
        assert await usage(w) == [("replan", True)]
        assert "Re-plan this date we loved: Date night. Stops: Tacos; Stargazing." in seen[0]
        assert "Use exactly 2 stops." in seen[0]
        plans = (await w.owner.get("/api/v1/plan/sessions")).json()
        (plan,) = [p for p in plans if p["itinerary_id"] == out["id"]]
        assert plan["inputs"]["budget_max"] == 150
        assert dt.datetime.fromisoformat(plan["inputs"]["window_start"]) == start
        end = dt.datetime.fromisoformat(plan["inputs"]["window_end"])
        assert end == start + dt.timedelta(hours=3)
        assert plan["inputs"]["must_include"] is None


async def test_replan_with_an_end_and_for_a_template_with_no_stops(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        template_id = (await save(w.owner, str(await seed(w)))).json()["id"]
        start = when(days=7, hour=12)
        end = start + dt.timedelta(hours=5)
        body = {"starts_at": start.isoformat(), "ends_at": end.isoformat()}
        with recording_ai(db_app) as seen:
            response = await call(w.owner, "POST", f"{TEMPLATES}/{template_id}/replan", body)
        assert response.status_code == 201, response.text
        assert "Use exactly" not in seen[0]
        plans = (await w.owner.get("/api/v1/plan/sessions")).json()
        assert dt.datetime.fromisoformat(plans[0]["inputs"]["window_end"]) == end


async def test_replan_is_only_for_your_own_template(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        template_id = (await save(w.owner, await seeded(w))).json()["id"]
        body = {"starts_at": when(days=7).isoformat()}
        db_app.state.settings.demo_mode = False  # no AI: the 404 must still come first
        response = await call(w.partner, "POST", f"{TEMPLATES}/{template_id}/replan", body)
        assert response.status_code == 404
        response = await call(w.owner, "POST", f"{TEMPLATES}/{template_id}/replan", body)
        assert response.status_code == 503


async def test_replan_needs_a_start_and_a_home_area(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        template_id = (await save(w.owner, await seeded(w))).json()["id"]
        path = f"{TEMPLATES}/{template_id}/replan"
        assert (await call(w.owner, "POST", path, {})).status_code == 422
        response = await call(w.owner, "POST", path, {"starts_at": when(days=7).isoformat()})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "home_area_required"
        async with w.app.state.sessionmaker() as session:
            assert (await session.execute(select(AIUsage))).first() is None
