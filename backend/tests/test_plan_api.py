"""The /plan API end to end, with the fake AI and fake providers."""

import datetime as dt
import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.ai.client import AIError, FakeAIClient, Usage, get_ai
from app.ai.schemas import ItineraryDraft, PlanOption, PlanOptions, PlanOptionStop
from app.models import AIUsage, ChecklistItem, Idea, Stop, TravelLeg
from tests.authutil import browser, call, profile_ids, sign_in
from tests.itinerary_helpers import (
    ALLOWED_VENUE_KEYS,
    DownPlaces,
    World,
    fake_providers,
    set_home,
    when,
    world,
)

pytestmark = pytest.mark.db

PLAN = "/api/v1/plan/sessions"


def session_body(**changes: Any) -> dict[str, Any]:
    start = when(days=5, hour=18)
    body: dict[str, Any] = {
        "window_start": start.isoformat(),
        "window_end": (start + dt.timedelta(hours=4)).isoformat(),
        "steer": "something cosy",
    }
    body.update(changes)
    return body


async def new_session(w: World, **changes: Any) -> dict[str, Any]:
    response = await call(w.owner, "POST", PLAN, session_body(**changes))
    assert response.status_code == 201, response.text
    out: dict[str, Any] = response.json()
    return out


async def with_options(w: World) -> tuple[str, dict[str, Any]]:
    plan = await new_session(w)
    response = await call(w.owner, "POST", f"{PLAN}/{plan['id']}/options", {})
    assert response.status_code == 200, response.text
    return plan["id"], response.json()


# --- Sessions


async def test_create_defaults_the_recipient_to_the_other_slot(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        profiles = await profile_ids(w.owner)
        plan = await new_session(w)
        assert plan["inputs"]["recipient_profile_id"] == profiles["partner"]
        assert plan["status"] == "open" and plan["options"] == [] and plan["chosen"] is None
        assert plan["itinerary_id"] is None
        # The partner plans for the owner's profile.
        other = await call(w.partner, "POST", PLAN, session_body())
        assert other.json()["inputs"]["recipient_profile_id"] == profiles["owner"]


async def test_window_rules(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        start = when(days=5)

        def body(end: dt.datetime) -> dict[str, Any]:
            return session_body(window_start=start.isoformat(), window_end=end.isoformat())

        too_long = await call(w.owner, "POST", PLAN, body(start + dt.timedelta(days=15)))
        backwards = await call(w.owner, "POST", PLAN, body(start - dt.timedelta(hours=1)))
        empty = await call(w.owner, "POST", PLAN, body(start))
        edge = await call(w.owner, "POST", PLAN, body(start + dt.timedelta(days=14)))
        assert [r.status_code for r in (too_long, backwards, empty, edge)] == [422, 422, 422, 201]
        assert too_long.json()["error"]["code"] == "invalid_window"


async def test_recipient_must_be_in_the_couple(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        response = await call(
            w.owner, "POST", PLAN, session_body(recipient_profile_id=str(uuid.uuid4()))
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_recipient"


async def test_input_limits(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        for bad in ({"steer": "x" * 501}, {"must_include": "x" * 201}, {"budget_max": -1}):
            response = await call(w.owner, "POST", PLAN, session_body(**bad))
            assert response.status_code == 422, bad


async def test_sessions_are_private_to_the_planner(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        plan = await new_session(w)
        path = f"{PLAN}/{plan['id']}"
        assert (await w.owner.get(path)).status_code == 200
        assert [p["id"] for p in (await w.owner.get(PLAN)).json()] == [plan["id"]]
        assert (await w.partner.get(PLAN)).json() == []
        async with browser(db_app) as outsider:
            await sign_in(db_app, outsider, "cat@example.com")
            assert (await outsider.get(PLAN)).json() == []
            for client in (w.partner, outsider):
                assert (await client.get(path)).status_code == 404
                for tail, body in (
                    ("/options", {}),
                    ("/build", {"option_ids": [str(uuid.uuid4())]}),
                    ("/plan-all", {}),
                ):
                    response = await call(client, "POST", path + tail, body)
                    assert response.status_code == 404, (tail, response.text)
        assert (await w.owner.get(path)).json()["status"] == "open"


# --- Options


async def test_options_returns_three_grounded_options_and_saves_ideas(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        plan_id, out = await with_options(w)
        assert len(out["options"]) == 3
        assert out["degraded"] == []
        assert all(uuid.UUID(o["id"]) for o in out["options"])
        assert out["options"][0]["grounded"] is True
        assert out["options"][2]["grounded"] is False  # the evergreen night in
        assert len((await w.owner.get(f"{PLAN}/{plan_id}")).json()["options"]) == 3

        async with w.app.state.sessionmaker() as session:
            ideas = list((await session.execute(select(Idea))).scalars())
        assert len(ideas) == 3
        assert {i.visibility for i in ideas} == {"private"}
        assert {i.source for i in ideas} == {"plan"}
        assert {i.generation["plan_session_id"] for i in ideas} == {plan_id}
        assert {i.created_by_user_id for i in ideas} == {w.owner_id}
        assert len((await w.owner.get("/api/v1/ideas")).json()) == 3
        assert (await w.partner.get("/api/v1/ideas")).json() == []


async def test_more_options_appends(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        plan_id, first = await with_options(w)
        response = await call(w.owner, "POST", f"{PLAN}/{plan_id}/options", {"more": True})
        assert response.status_code == 200
        out = response.json()
        assert len(out["options"]) == 6
        assert out["options"][:3] == first["options"]
        assert len({o["id"] for o in out["options"]}) == 6


async def test_options_needs_a_home_area(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        plan = await new_session(w)
        response = await call(w.owner, "POST", f"{PLAN}/{plan['id']}/options", {})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "home_area_required"
        async with w.app.state.sessionmaker() as session:
            assert (await session.execute(select(AIUsage))).first() is None  # nothing reserved


async def test_options_reports_down_providers(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        w.providers["p"] = fake_providers(places=DownPlaces())
        _, out = await with_options(w)
        assert out["degraded"] == ["places"]
        assert len(out["options"]) == 3  # evergreen fallbacks, nothing grounded
        assert not any(o["grounded"] for o in out["options"])


# --- Build and plan-all


async def test_build_creates_a_draft_itinerary(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        plan_id, out = await with_options(w)
        chosen = [out["options"][0]["id"], out["options"][1]["id"]]
        note = "the first for dinner, the walk after"
        response = await call(
            w.owner,
            "POST",
            f"{PLAN}/{plan_id}/build",
            {"option_ids": chosen, "combine_note": note},
        )
        assert response.status_code == 200, response.text
        itinerary = response.json()
        assert itinerary["status"] == "draft"
        assert itinerary["is_planner"] is True and itinerary["is_surprise"] is False
        assert itinerary["stops"] and itinerary["invitation_message"]
        assert itinerary["backup_plan"] and itinerary["bring_wear"]
        assert itinerary["currency"] == "USD"
        assert itinerary["starts_at"] == itinerary["stops"][0]["starts_at"]
        assert itinerary["checklist"]
        assert any(s["travel_to_next"] for s in itinerary["stops"][:-1])

        plan = (await w.owner.get(f"{PLAN}/{plan_id}")).json()
        assert plan["status"] == "done" and plan["itinerary_id"] == itinerary["id"]
        assert plan["chosen"] == {"option_ids": chosen, "combine_note": note}

        async with w.app.state.sessionmaker() as session:
            stops = list((await session.execute(select(Stop).order_by(Stop.position))).scalars())
            legs = list((await session.execute(select(TravelLeg))).scalars())
            checklist = list((await session.execute(select(ChecklistItem))).scalars())
        assert stops
        for stop in stops:
            assert stop.booking_url is None
            if stop.venue is not None:
                assert set(stop.venue) == ALLOWED_VENUE_KEYS
        located = [s for s in stops if s.venue]
        assert legs and len(legs) == len(located) - 1
        assert {leg.mode for leg in legs} == {"drive"}
        assert checklist and all(c.link is None for c in checklist)
        # The partner cannot see an unannounced draft.
        assert (await w.partner.get(f"/api/v1/itineraries/{itinerary['id']}")).status_code == 404


async def test_plan_all_builds_without_options(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        plan = await new_session(w, surprise=True, budget_max=200)
        response = await call(w.owner, "POST", f"{PLAN}/{plan['id']}/plan-all", {})
        assert response.status_code == 200, response.text
        itinerary = response.json()
        assert itinerary["status"] == "draft" and len(itinerary["stops"]) >= 1
        assert itinerary["is_surprise"] is True and itinerary["budget"] == 200
        after = (await w.owner.get(f"{PLAN}/{plan['id']}")).json()
        assert after["status"] == "done" and after["itinerary_id"] == itinerary["id"]
        async with w.app.state.sessionmaker() as session:
            used = list((await session.execute(select(AIUsage))).scalars())
        assert [(u.feature, u.ok) for u in used] == [("plan_all", True)]


async def test_build_rejects_an_unknown_option(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        plan_id, out = await with_options(w)
        response = await call(
            w.owner,
            "POST",
            f"{PLAN}/{plan_id}/build",
            {"option_ids": [out["options"][0]["id"], str(uuid.uuid4())]},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
        assert (await w.owner.get(f"{PLAN}/{plan_id}")).json()["status"] == "open"
        async with w.app.state.sessionmaker() as session:
            used = list((await session.execute(select(AIUsage.feature))).scalars())
        assert used == ["plan_options"]  # the failed build never reserved a run


async def test_build_body_limits(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        plan = await new_session(w)
        path = f"{PLAN}/{plan['id']}/build"
        ids = [str(uuid.uuid4()) for _ in range(4)]
        for body in (
            {"option_ids": []},
            {"option_ids": ids},
            {"option_ids": ids[:1], "combine_note": "x" * 301},
        ):
            assert (await call(w.owner, "POST", path, body)).status_code == 422, body


# --- Failure and caps


async def test_a_failing_run_is_recorded_and_returns_502(db_app: FastAPI) -> None:
    async def refuse(*args: Any) -> Any:
        raise AIError("refused", "no", usage=Usage(model="fake", input=40, output=7), tool_calls=2)

    async with world(db_app) as w:
        await set_home(w)
        db_app.dependency_overrides[get_ai] = lambda: FakeAIClient({ItineraryDraft: refuse})
        try:
            plan = await new_session(w)
            response = await call(w.owner, "POST", f"{PLAN}/{plan['id']}/plan-all", {})
        finally:
            db_app.dependency_overrides.pop(get_ai, None)
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "ai_failed"
        async with w.app.state.sessionmaker() as session:
            used = list((await session.execute(select(AIUsage))).scalars())
        assert len(used) == 1
        assert (used[0].feature, used[0].ok, used[0].tool_calls) == ("plan_all", False, 2)
        assert (used[0].input_tokens, used[0].output_tokens) == (40, 7)
        after = (await w.owner.get(f"{PLAN}/{plan['id']}")).json()
        assert after["status"] == "open" and after["itinerary_id"] is None


async def test_the_build_cap_returns_429(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        plan = await new_session(w)
        db_app.state.settings.ai_cap_builds_per_day = 1
        first = await call(w.owner, "POST", f"{PLAN}/{plan['id']}/options", {})
        assert first.status_code == 200
        chosen = first.json()["options"][0]["id"]
        for tail, body in (
            ("/options", {}),
            ("/plan-all", {}),
            ("/build", {"option_ids": [chosen]}),
        ):
            response = await call(w.owner, "POST", f"{PLAN}/{plan['id']}{tail}", body)
            assert response.status_code == 429, (tail, response.text)
            assert response.json()["error"]["code"] == "ai_cap_reached"
            assert "reset_at" in response.json()["error"]


async def test_ai_unavailable_is_503(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        plan = await new_session(w)
        db_app.state.settings.demo_mode = False  # no key and no demo mode: no AI client
        response = await call(w.owner, "POST", f"{PLAN}/{plan['id']}/plan-all", {})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "ai_unavailable"


async def test_someone_elses_plan_is_404_even_when_ai_is_unavailable(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        plan = await new_session(w)
        db_app.state.settings.demo_mode = False  # no AI client: ownership must still come first
        for tail in ("/options", "/plan-all", "/build"):
            body = {"option_ids": [str(uuid.uuid4())]} if tail == "/build" else {}
            response = await call(w.partner, "POST", f"{PLAN}/{plan['id']}{tail}", body)
            assert response.status_code == 404, (tail, response.text)


async def test_event_options_show_their_event_in_ideas(db_app: FastAPI) -> None:
    async def respond(tools: Any, context: str, messages: Any) -> PlanOptions:
        start = when(days=5, hour=18)
        found = json.loads(
            await tools.execute(
                "search_events",
                {"start": start.isoformat(), "end": (start + dt.timedelta(days=1)).isoformat()},
            )
        )["data"]
        assert found
        event = found[0]
        options = [
            PlanOption(
                title=f"Option {n}",
                pitch="A date.",
                why_for_them="They will love it.",
                stops=[
                    PlanOptionStop(
                        title=event["name"],
                        kind="event",
                        event_id=event["event_id"] if n == 0 else "made-up",
                        evergreen=False,
                    )
                ],
                grounded=True,
            )
            for n in range(3)
        ]
        respond.event = event  # type: ignore[attr-defined]
        return PlanOptions(options=options)

    async with world(db_app) as w:
        await set_home(w)
        db_app.dependency_overrides[get_ai] = lambda: FakeAIClient({PlanOptions: respond})
        try:
            plan = await new_session(w)
            response = await call(w.owner, "POST", f"{PLAN}/{plan['id']}/options", {})
        finally:
            db_app.dependency_overrides.pop(get_ai, None)
        assert response.status_code == 200, response.text
        event = respond.event  # type: ignore[attr-defined]
        ideas = (await w.owner.get("/api/v1/ideas")).json()
        with_event = [i for i in ideas if i["event"] is not None]
        assert len(ideas) == 3 and len(with_event) == 1
        assert with_event[0]["event"]["name"] == event["name"]
        assert with_event[0]["event"]["local_date"] == event["local_date"]


async def test_an_unavailable_ai_mid_run_is_recorded_and_returns_503(db_app: FastAPI) -> None:
    async def down(*args: Any) -> Any:
        raise AIError("ai_unavailable", "network", tool_calls=1)

    async with world(db_app) as w:
        await set_home(w)
        db_app.dependency_overrides[get_ai] = lambda: FakeAIClient({ItineraryDraft: down})
        try:
            plan = await new_session(w)
            response = await call(w.owner, "POST", f"{PLAN}/{plan['id']}/plan-all", {})
        finally:
            db_app.dependency_overrides.pop(get_ai, None)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "ai_unavailable"
        async with w.app.state.sessionmaker() as session:
            used = list((await session.execute(select(AIUsage))).scalars())
        assert [(u.feature, u.ok) for u in used] == [("plan_all", False)]
