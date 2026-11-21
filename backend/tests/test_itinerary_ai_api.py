"""Regenerating one stop and polishing the invitation, with the fake AI and fake providers."""

import datetime as dt
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.ai.client import FAKE_RESPONDERS, AIError, FakeAIClient, Responder, get_ai
from app.ai.schemas import DraftStop, ItineraryDraft, PolishedInvitation
from app.errors import ApiError
from app.models import AIUsage, Stop
from tests.authutil import call
from tests.itinerary_helpers import (
    GELATO,
    ITINERARIES,
    TRATTORIA,
    World,
    seed,
    set_home,
    when,
    world,
)

pytestmark = pytest.mark.db


@contextmanager
def fake_ai(app: FastAPI, responders: dict[Any, Responder]) -> Iterator[None]:
    app.dependency_overrides[get_ai] = lambda: FakeAIClient(responders)
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_ai, None)


@contextmanager
def no_ai(app: FastAPI) -> Iterator[None]:
    """The AI dependency as it is with no API key and no demo mode: it raises a 503."""

    def unavailable() -> None:
        raise ApiError(503, "ai_unavailable", "The planner isn't available right now.")

    app.dependency_overrides[get_ai] = unavailable
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_ai, None)


async def dated(w: World, **fields: Any) -> tuple[uuid.UUID, list[dict[str, Any]]]:
    """An announced, scheduled date with three stops: a venue, an evergreen stop, a venue."""
    stops = [
        {
            "title": "Dinner",
            "place_id": TRATTORIA,
            "starts_at": when(hour=18),
            "ends_at": when(hour=19),
            "description": "Pasta.",
        },
        {
            "title": "A slow walk",
            "starts_at": when(hour=19, days=10) + dt.timedelta(minutes=30),
            "ends_at": when(hour=20, days=10) + dt.timedelta(minutes=30),
        },
        {
            "title": "Gelato",
            "place_id": GELATO,
            "starts_at": when(hour=21),
            "ends_at": when(hour=22),
        },
    ]
    values: dict[str, Any] = {
        "status": "scheduled",
        "announced_at": when(-1),
        "starts_at": when(hour=18),
        "ends_at": when(hour=22),
    }
    values.update(fields)
    itinerary_id = await seed(w, stops=stops, **values)
    body = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()
    return itinerary_id, body["stops"]


def regenerate_path(itinerary_id: Any, stop_id: Any) -> str:
    return f"{ITINERARIES}/{itinerary_id}/stops/{stop_id}/regenerate"


def swapping(seen: list[Any]) -> Responder:
    """A responder that returns the itinerary it was given with the chosen stop replaced by
    another place it found with the search tool."""

    async def respond(tools: Any, context: str, messages: Any) -> ItineraryDraft:
        seen.append(messages)
        data = json.loads(messages[0]["content"].split("\n", 1)[1])
        stops = data["itinerary"]["stops"]
        index = data["replace_stop_index"]
        found = json.loads(
            await tools.execute("search_places", {"query": "taqueria", "max_results": 4})
        )["data"]
        other = next(p for p in found if p["place_id"] != stops[index]["place_id"])
        draft = [
            DraftStop(
                title="Brand new stop" if i == index else f"changed {s['title']}",
                place_id=other["place_id"] if i == index else s["place_id"],
                evergreen=False if i == index else s["place_id"] is None,
                starts_at=s["starts_at"],
                ends_at=s["ends_at"],
                description="New description.",
                est_cost=12.5 if i == index else None,
                notes="Bring cash." if i == index else None,
            )
            for i, s in enumerate(stops)
        ]
        return ItineraryDraft(title="x", stops=draft, transport_mode="drive")

    return respond


# --- Regenerate a stop


async def test_regenerate_replaces_only_that_stop(db_app: FastAPI) -> None:
    seen: list[Any] = []
    async with world(db_app) as w:
        await set_home(w)
        itinerary_id, before = await dated(w)
        with fake_ai(db_app, {ItineraryDraft: swapping(seen)}):
            response = await call(
                w.owner,
                "POST",
                regenerate_path(itinerary_id, before[0]["id"]),
                {"note": "something outdoorsy"},
            )
        assert response.status_code == 200, response.text
        after = response.json()["stops"]
        assert len(after) == 3
        assert after[0]["id"] != before[0]["id"]
        assert after[0]["title"] == "Brand new stop" and after[0]["est_cost"] == 12.5
        assert after[0]["description"] == "New description."
        assert after[0]["notes"] == "Bring cash."
        assert [s["notes"] for s in after[1:]] == [None, None]
        assert after[0]["place_id"] != TRATTORIA and after[0]["place_id"] is not None
        assert after[0]["venue"] is not None
        assert (after[0]["starts_at"], after[0]["ends_at"]) == (
            before[0]["starts_at"],
            before[0]["ends_at"],
        )
        # The others keep their ids and everything the model said about them is ignored.
        for old, new in zip(before[1:], after[1:], strict=True):
            assert new["id"] == old["id"] and new["title"] == old["title"]
            assert new["description"] == old["description"]
        assert "something outdoorsy" in json.dumps(seen[0])
        async with w.app.state.sessionmaker() as session:
            used = list((await session.execute(select(AIUsage))).scalars())
        assert [(u.feature, u.ok) for u in used] == [("regenerate_stop", True)]


async def test_regenerate_works_without_a_body(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        itinerary_id, before = await dated(w)
        with fake_ai(db_app, {ItineraryDraft: swapping([])}):
            response = await call(w.owner, "POST", regenerate_path(itinerary_id, before[2]["id"]))
        assert response.status_code == 200, response.text
        assert response.json()["stops"][2]["title"] == "Brand new stop"


async def test_regenerate_is_for_the_planner_only(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        itinerary_id, before = await dated(w)
        hidden_id, hidden = await dated(w, announced_at=None, status="draft")
        with fake_ai(db_app, {ItineraryDraft: swapping([])}):
            seen = await call(w.partner, "POST", regenerate_path(itinerary_id, before[0]["id"]))
            unseen = await call(w.partner, "POST", regenerate_path(hidden_id, hidden[0]["id"]))
        assert seen.status_code == 403
        assert unseen.status_code == 404
        async with w.app.state.sessionmaker() as session:
            assert list((await session.execute(select(AIUsage))).scalars()) == []


async def test_regenerate_a_stop_of_another_itinerary_is_404(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        first, _ = await dated(w)
        _, other_stops = await dated(w)
        with fake_ai(db_app, {ItineraryDraft: swapping([])}):
            response = await call(w.owner, "POST", regenerate_path(first, other_stops[0]["id"]))
            missing = await call(w.owner, "POST", regenerate_path(first, uuid.uuid4()))
        assert response.status_code == 404 and missing.status_code == 404


async def test_regenerate_needs_an_editable_date(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        itinerary_id, before = await dated(w, status="cancelled")
        with fake_ai(db_app, {ItineraryDraft: swapping([])}):
            response = await call(w.owner, "POST", regenerate_path(itinerary_id, before[0]["id"]))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_editable"


async def test_regenerate_with_the_ai_unavailable_is_503(db_app: FastAPI) -> None:
    async def down(*args: Any) -> Any:
        raise AIError("ai_unavailable", "network")

    async with world(db_app) as w:
        await set_home(w)
        itinerary_id, before = await dated(w)
        with fake_ai(db_app, {ItineraryDraft: down}):
            response = await call(w.owner, "POST", regenerate_path(itinerary_id, before[0]["id"]))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "ai_unavailable"
        async with w.app.state.sessionmaker() as session:
            used = list((await session.execute(select(AIUsage))).scalars())
        assert [(u.feature, u.ok) for u in used] == [("regenerate_stop", False)]
        after = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()["stops"]
        assert [s["id"] for s in after] == [s["id"] for s in before]


async def test_a_stop_count_mismatch_is_502_and_changes_nothing(db_app: FastAPI) -> None:
    async def short(tools: Any, context: str, messages: Any) -> ItineraryDraft:
        data = json.loads(messages[0]["content"].split("\n", 1)[1])
        first = data["itinerary"]["stops"][0]
        return ItineraryDraft(
            title="x",
            stops=[
                DraftStop(
                    title="Only one",
                    evergreen=True,
                    starts_at=first["starts_at"],
                    ends_at=first["ends_at"],
                    description="Just this.",
                )
            ],
            transport_mode="drive",
        )

    async with world(db_app) as w:
        await set_home(w)
        itinerary_id, before = await dated(w)
        with fake_ai(db_app, {ItineraryDraft: short}):
            response = await call(w.owner, "POST", regenerate_path(itinerary_id, before[0]["id"]))
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "ai_failed"
        after = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()["stops"]
        assert [s["id"] for s in after] == [s["id"] for s in before]


async def test_regenerate_note_over_300_is_422(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id, before = await dated(w)
        with fake_ai(db_app, {ItineraryDraft: swapping([])}):
            response = await call(
                w.owner, "POST", regenerate_path(itinerary_id, before[0]["id"]), {"note": "x" * 301}
            )
        assert response.status_code == 422


# --- Polish the invitation


def polish_path(itinerary_id: Any) -> str:
    return f"{ITINERARIES}/{itinerary_id}/polish-invitation"


async def test_polish_returns_text_and_records_the_run(db_app: FastAPI) -> None:
    seen: list[Any] = []

    async def polish(tools: Any, context: str, messages: Any) -> PolishedInvitation:
        seen.append((tools, messages))
        return PolishedInvitation(text="Be mine on Friday?")

    async with world(db_app) as w:
        itinerary_id, _ = await dated(w)  # no home area needed
        with fake_ai(db_app, {PolishedInvitation: polish}):
            response = await call(
                w.owner, "POST", polish_path(itinerary_id), {"draft": "dinner friday?"}
            )
        assert response.status_code == 200, response.text
        assert response.json() == {"text": "Be mine on Friday?"}
        assert seen[0][0] is None and "dinner friday?" in json.dumps(seen[0][1])
        async with w.app.state.sessionmaker() as session:
            used = list((await session.execute(select(AIUsage))).scalars())
        assert [(u.feature, u.ok, u.tool_calls) for u in used] == [("polish", True, 0)]


async def test_polish_draft_limits_are_422(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id, _ = await dated(w)
        with fake_ai(db_app, {PolishedInvitation: lambda *a: None}):  # type: ignore[dict-item,misc]
            for draft in ("x" * 1001, "", "   "):
                response = await call(w.owner, "POST", polish_path(itinerary_id), {"draft": draft})
                assert response.status_code == 422, draft


async def test_polish_is_for_the_planner_only(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id, _ = await dated(w)
        hidden_id, _ = await dated(w, announced_at=None, status="draft")
        with fake_ai(db_app, {PolishedInvitation: lambda *a: None}):  # type: ignore[dict-item,misc]
            seen = await call(w.partner, "POST", polish_path(itinerary_id), {"draft": "hi"})
            unseen = await call(w.partner, "POST", polish_path(hidden_id), {"draft": "hi"})
        assert seen.status_code == 403 and unseen.status_code == 404
        async with w.app.state.sessionmaker() as session:
            assert list((await session.execute(select(AIUsage))).scalars()) == []


async def test_polish_with_the_ai_unavailable_is_503(db_app: FastAPI) -> None:
    async def down(*args: Any) -> Any:
        raise AIError("ai_unavailable", "network")

    async with world(db_app) as w:
        itinerary_id, _ = await dated(w)
        with fake_ai(db_app, {PolishedInvitation: down}):
            response = await call(w.owner, "POST", polish_path(itinerary_id), {"draft": "hi"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "ai_unavailable"
        async with w.app.state.sessionmaker() as session:
            used = list((await session.execute(select(AIUsage))).scalars())
        assert [(u.feature, u.ok) for u in used] == [("polish", False)]


async def test_polish_needs_an_editable_date(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id, _ = await dated(w, status="completed")
        with fake_ai(db_app, {PolishedInvitation: lambda *a: None}):  # type: ignore[dict-item,misc]
            response = await call(w.owner, "POST", polish_path(itinerary_id), {"draft": "hi"})
        assert response.status_code == 409


def with_replacement(messages: Any, **replacement: Any) -> ItineraryDraft:
    """The itinerary from the request with the chosen stop built from `replacement`."""
    data = json.loads(messages[0]["content"].split("\n", 1)[1])
    stops = data["itinerary"]["stops"]
    index = data["replace_stop_index"]
    draft = [
        DraftStop(
            title=s["title"],
            place_id=s["place_id"],
            evergreen=s["place_id"] is None,
            starts_at=s["starts_at"],
            ends_at=s["ends_at"],
            description=s["description"] or "Same as before.",
        )
        if i != index
        else DraftStop(
            starts_at=s["starts_at"],
            ends_at=s["ends_at"],
            description="New description.",
            **replacement,
        )
        for i, s in enumerate(stops)
    ]
    return ItineraryDraft(title="x", stops=draft, transport_mode="drive")


async def test_regenerate_a_seven_stop_date(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        stops = [
            {
                "title": f"Stop {n}",
                "place_id": TRATTORIA if n == 0 else None,
                "starts_at": when(hour=9 + n),
                "ends_at": when(hour=9 + n) + dt.timedelta(minutes=45),
            }
            for n in range(7)
        ]
        itinerary_id = await seed(
            w, stops=stops, status="draft", starts_at=when(hour=9), ends_at=when(hour=16)
        )
        before = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()["stops"]
        assert len(before) == 7
        with fake_ai(db_app, {ItineraryDraft: swapping([])}):
            response = await call(w.owner, "POST", regenerate_path(itinerary_id, before[3]["id"]))
        assert response.status_code == 200, response.text
        after = response.json()["stops"]
        assert len(after) == 7
        assert after[3]["id"] != before[3]["id"] and after[3]["title"] == "Brand new stop"
        for i in (0, 1, 2, 4, 5, 6):
            assert after[i]["id"] == before[i]["id"]
            assert (after[i]["starts_at"], after[i]["ends_at"]) == (
                before[i]["starts_at"],
                before[i]["ends_at"],
            )


async def test_a_replacement_event_keeps_its_event_data_and_venue(db_app: FastAPI) -> None:
    found: dict[str, Any] = {}

    async def respond(tools: Any, context: str, messages: Any) -> ItineraryDraft:
        start = when(hour=0)
        events = json.loads(
            await tools.execute(
                "search_events",
                {"start": start.isoformat(), "end": (start + dt.timedelta(days=1)).isoformat()},
            )
        )["data"]
        assert events
        found.update(events[0])
        return with_replacement(
            messages, title=events[0]["name"], event_id=events[0]["event_id"], evergreen=False
        )

    async with world(db_app) as w:
        await set_home(w)
        itinerary_id, before = await dated(w)
        with fake_ai(db_app, {ItineraryDraft: respond}):
            response = await call(w.owner, "POST", regenerate_path(itinerary_id, before[1]["id"]))
        assert response.status_code == 200, response.text
        replaced = response.json()["stops"][1]
        assert replaced["id"] != before[1]["id"] and replaced["place_id"] is None
        assert replaced["event"] is not None and replaced["event"]["name"] == found["name"]
        assert replaced["venue"] is not None
        async with w.app.state.sessionmaker() as session:
            row = (
                await session.execute(select(Stop).where(Stop.id == uuid.UUID(replaced["id"])))
            ).scalar_one()
        assert row.evergreen is False and set(row.venue) == {"lat", "lng", "fetched_at"}


async def test_ids_the_run_never_saw_are_dropped_and_the_stop_is_evergreen(
    db_app: FastAPI,
) -> None:
    async def respond(tools: Any, context: str, messages: Any) -> ItineraryDraft:
        return with_replacement(
            messages, title="Made up", place_id="made-up", event_id="also-made-up", evergreen=False
        )

    async with world(db_app) as w:
        await set_home(w)
        itinerary_id, before = await dated(w)
        with fake_ai(db_app, {ItineraryDraft: respond}):
            response = await call(w.owner, "POST", regenerate_path(itinerary_id, before[0]["id"]))
        assert response.status_code == 200, response.text
        replaced = response.json()["stops"][0]
        assert replaced["place_id"] is None and replaced["event"] is None
        assert replaced["venue"] is None
        async with w.app.state.sessionmaker() as session:
            row = (
                await session.execute(select(Stop).where(Stop.id == uuid.UUID(replaced["id"])))
            ).scalar_one()
        assert row.evergreen is True


async def test_what_is_still_wrong_after_the_repair_is_stored_as_warnings(db_app: FastAPI) -> None:
    async def respond(tools: Any, context: str, messages: Any) -> ItineraryDraft:
        # A place nobody returned: the stop is flagged "ungrounded" and stays so after the repair.
        return with_replacement(messages, title="Mystery spot", place_id="made-up", evergreen=False)

    async with world(db_app) as w:
        await set_home(w)
        itinerary_id, before = await dated(
            w,
            warnings=[
                {"stop_index": 1, "code": "closed", "message": "stale"},
                {"stop_index": 2, "code": "closed", "message": "kept"},
            ],
        )
        with fake_ai(db_app, {ItineraryDraft: respond}):
            response = await call(w.owner, "POST", regenerate_path(itinerary_id, before[1]["id"]))
        assert response.status_code == 200, response.text
        warnings = response.json()["warnings"]
        # The other stops are flagged "ungrounded" in the draft too, but only the replaced stop's
        # issues are taken from this run.
        assert [(x["stop_index"], x["code"]) for x in warnings] == [
            (2, "closed"),
            (1, "ungrounded"),
        ]


async def test_a_blank_replacement_title_is_a_handled_failure(db_app: FastAPI) -> None:
    async def respond(tools: Any, context: str, messages: Any) -> ItineraryDraft:
        return with_replacement(messages, title="   ", evergreen=True)

    async with world(db_app) as w:
        await set_home(w)
        itinerary_id, before = await dated(w)
        with fake_ai(db_app, {ItineraryDraft: respond}):
            response = await call(w.owner, "POST", regenerate_path(itinerary_id, before[0]["id"]))
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "ai_failed"
        after = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()["stops"]
        assert [s["id"] for s in after] == [s["id"] for s in before]


# --- Access errors win over an unavailable planner


async def test_access_errors_come_before_a_503_from_the_ai_dependency(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        shown_id, shown = await dated(w)
        hidden_id, hidden = await dated(w, announced_at=None, status="draft")
        cancelled_id, cancelled = await dated(w, status="cancelled")
        with no_ai(db_app):
            for owner_client, itinerary_id, stops, expected in (
                (w.partner, shown_id, shown, 403),
                (w.partner, hidden_id, hidden, 404),
                (w.owner, cancelled_id, cancelled, 409),
            ):
                regen = await call(
                    owner_client, "POST", regenerate_path(itinerary_id, stops[0]["id"])
                )
                polish = await call(
                    owner_client, "POST", polish_path(itinerary_id), {"draft": "hi"}
                )
                assert regen.status_code == expected, (expected, regen.text)
                assert polish.status_code == expected, (expected, polish.text)
            # With nothing wrong with the request, the 503 is what the planner gets.
            ok = await call(w.owner, "POST", regenerate_path(shown_id, shown[0]["id"]))
            assert ok.status_code == 503


# --- Demo mode


async def test_demo_mode_regenerate_keeps_a_four_stop_date_intact(db_app: FastAPI) -> None:
    import app.ai.fakes  # noqa: F401  (importing registers the demo responders)

    calls: list[int] = []

    class CountingAI(FakeAIClient):
        async def run(self, **kwargs: Any) -> Any:  # type: ignore[override]
            calls.append(1)
            return await super().run(**kwargs)

    async with world(db_app) as w:
        await set_home(w)
        stops = [
            {
                "title": f"Stop {n}",
                "place_id": place_id,
                "starts_at": when(hour=18 + n),
                "ends_at": when(hour=18 + n) + dt.timedelta(minutes=45),
            }
            for n, place_id in enumerate((TRATTORIA, None, GELATO, None))
        ]
        itinerary_id = await seed(
            w, stops=stops, status="draft", starts_at=when(hour=18), ends_at=when(hour=23)
        )
        before = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()["stops"]
        db_app.dependency_overrides[get_ai] = lambda: CountingAI(FAKE_RESPONDERS)
        try:
            response = await call(w.owner, "POST", regenerate_path(itinerary_id, before[1]["id"]))
        finally:
            db_app.dependency_overrides.pop(get_ai, None)
        assert response.status_code == 200, response.text
        # The other stops are stripped of their unseen place ids, so the repair pass ran too.
        assert len(calls) == 2
        after = response.json()["stops"]
        assert len(after) == 4
        assert after[1]["id"] != before[1]["id"]
        assert [s["id"] for i, s in enumerate(after) if i != 1] == [
            s["id"] for i, s in enumerate(before) if i != 1
        ]
        assert after[1]["starts_at"] == before[1]["starts_at"]
