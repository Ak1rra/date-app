"""Surprise mode, the surprisee's side: the visibility rule, the redacted view, the calendar
entry, per-stop redaction and the hidden surprise ideas."""

import datetime as dt
import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy import select

from app.ai.context import build_context
from app.models import Idea, Itinerary, Stop, SurpriseHint, TravelLeg, Vote
from app.services.itinerary_access import upcoming_surprises
from tests.authutil import browser, call, profile_ids, sign_in
from tests.itinerary_helpers import ITINERARIES, TRATTORIA, World, seed, when, world

pytestmark = pytest.mark.db

SURPRISES = "/api/v1/surprises"
CALENDAR = "/api/v1/calendar"
IDEAS = "/api/v1/ideas"
SECRET = "Secret Pottery Night"
SECRET_STOP = "Secret Trattoria"


def surprise_fields(**more: Any) -> dict[str, Any]:
    return {
        "title": SECRET,
        "is_surprise": True,
        "status": "scheduled",
        "starts_at": when(),
        "ends_at": when() + dt.timedelta(hours=3),
        "reveal_at": when(),
        **more,
    }


async def add_hint(
    w: World, itinerary_id: uuid.UUID, text: str, released_at: dt.datetime | None
) -> None:
    async with w.app.state.sessionmaker() as session:
        session.add(
            SurpriseHint(
                itinerary_id=itinerary_id,
                kind="bring",
                text=text,
                release_at=released_at or when(1),
                released_at=released_at,
            )
        )
        await session.commit()


async def reveal(w: World, itinerary_id: uuid.UUID) -> None:
    async with w.app.state.sessionmaker() as session:
        itinerary = await session.get(Itinerary, itinerary_id)
        assert itinerary is not None
        itinerary.revealed_at = dt.datetime.now(dt.UTC)
        await session.commit()


async def calendar(client: AsyncClient) -> Response:
    response = await client.get(
        CALENDAR,
        params={"from": when(-1).date().isoformat(), "to": when(30).date().isoformat()},
    )
    assert response.status_code == 200, response.text
    return response


async def test_an_unrevealed_surprise_is_hidden_from_the_surprisee_except_its_view(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        stop = {"title": SECRET_STOP, "place_id": TRATTORIA, "notes": "hush"}
        sid = await seed(w, stops=[stop], **surprise_fields())
        now = dt.datetime.now(dt.UTC)
        await add_hint(w, sid, "Wear comfy shoes", now - dt.timedelta(hours=1))
        await add_hint(w, sid, "Early hint", now - dt.timedelta(hours=3))
        await add_hint(w, sid, "Not yet released", None)

        assert (await w.partner.get(f"{ITINERARIES}/{sid}")).status_code == 404
        listing = await w.partner.get(ITINERARIES)
        assert listing.json() == []

        cal = await calendar(w.partner)
        entries = [e for e in cal.json() if e["itinerary_id"] == str(sid)]
        assert len(entries) == 1
        assert entries[0]["title"] == "Surprise date ✨"
        assert entries[0]["is_surprise"] is True
        assert entries[0]["announced"] is False
        assert entries[0]["status"] == "scheduled"
        assert [e["title"] for e in cal.json()] == ["Surprise date ✨"]

        view = await w.partner.get(SURPRISES)
        assert view.status_code == 200
        assert [s["id"] for s in view.json()] == [str(sid)]
        assert [h["text"] for h in view.json()[0]["hints"]] == ["Early hint", "Wear comfy shoes"]
        assert set(view.json()[0]) == {"id", "starts_at", "ends_at", "reveal_at", "hints"}
        one = await w.partner.get(f"{SURPRISES}/{sid}")
        assert one.status_code == 200
        assert one.json()["id"] == str(sid)
        for response in (view, one, listing, cal):
            for secret in (SECRET, SECRET_STOP, "Not yet released", "hush"):
                assert secret not in response.text


async def test_after_the_reveal_the_surprise_is_an_ordinary_date(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        sid = await seed(w, **surprise_fields())
        assert (await w.partner.get(f"{SURPRISES}/{sid}")).status_code == 200
        await reveal(w, sid)

        got = await w.partner.get(f"{ITINERARIES}/{sid}")
        assert got.status_code == 200
        assert got.json()["title"] == SECRET
        assert [i["title"] for i in (await w.partner.get(ITINERARIES)).json()] == [SECRET]
        entries = (await calendar(w.partner)).json()
        assert [(e["title"], e["itinerary_id"]) for e in entries] == [(SECRET, str(sid))]
        assert (await w.partner.get(SURPRISES)).json() == []
        assert (await w.partner.get(f"{SURPRISES}/{sid}")).status_code == 404


async def test_the_planner_sees_their_own_surprise_in_full_and_not_in_the_view(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        sid = await seed(w, **surprise_fields())
        assert (await w.owner.get(f"{ITINERARIES}/{sid}")).json()["title"] == SECRET
        assert [e["title"] for e in (await calendar(w.owner)).json()] == [SECRET]
        assert (await w.owner.get(SURPRISES)).json() == []
        assert (await w.owner.get(f"{SURPRISES}/{sid}")).status_code == 404
        async with db_app.state.sessionmaker() as session:
            rows = await session.execute(upcoming_surprises(session, w.owner_id, w.couple_id))
            assert rows.scalars().all() == []


async def test_a_draft_or_cancelled_surprise_is_invisible_to_the_surprisee(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        draft = await seed(w, **surprise_fields(status="draft", starts_at=None, ends_at=None))
        unscheduled = await seed(w, **surprise_fields(status="draft"))
        cancelled = await seed(w, **surprise_fields(status="cancelled"))
        for itinerary_id in (draft, unscheduled, cancelled):
            assert (await w.partner.get(f"{ITINERARIES}/{itinerary_id}")).status_code == 404
            assert (await w.partner.get(f"{SURPRISES}/{itinerary_id}")).status_code == 404
        assert (await w.partner.get(SURPRISES)).json() == []
        assert (await w.partner.get(ITINERARIES)).json() == []
        assert (await calendar(w.partner)).json() == []


async def test_a_surprise_is_only_for_its_couple(db_app: FastAPI) -> None:
    async with world(db_app) as w, browser(db_app) as stranger:
        sid = await seed(w, **surprise_fields())
        await sign_in(db_app, stranger, "zed@example.com")
        assert (await stranger.get(SURPRISES)).json() == []
        assert (await stranger.get(f"{SURPRISES}/{sid}")).status_code == 404


def stop_dicts(*, future: bool) -> list[dict[str, Any]]:
    """Three stops an hour apart; the middle one is at_stop_time, with everything to hide."""
    base = when(10) if future else when(-1)
    stops: list[dict[str, Any]] = [
        {"title": "Opening", "place_id": TRATTORIA},
        {
            "title": SECRET_STOP,
            "reveal_policy": "at_stop_time",
            "place_id": TRATTORIA,
            "notes": "hush",
            "description": "a secret",
            "est_cost": 40,
            "event": {"url": "https://tickets.example/x"},
            "warnings": [{"code": "w"}],
        },
        {"title": "Dessert", "place_id": TRATTORIA},
    ]
    for index, stop in enumerate(stops):
        stop["starts_at"] = base + dt.timedelta(hours=index)
    return stops


async def add_legs(w: World, itinerary_id: uuid.UUID) -> None:
    """A walking leg from each stop to the next."""
    async with w.app.state.sessionmaker() as session:
        stops = (
            (
                await session.execute(
                    select(Stop).where(Stop.itinerary_id == itinerary_id).order_by(Stop.position)
                )
            )
            .scalars()
            .all()
        )
        for here, there in zip(stops, stops[1:], strict=False):
            session.add(
                TravelLeg(
                    itinerary_id=itinerary_id,
                    from_stop_id=here.id,
                    to_stop_id=there.id,
                    mode="walk",
                    duration_minutes=10,
                    distance_m=700,
                )
            )
        await session.commit()


async def test_an_upcoming_at_stop_time_stop_is_redacted_for_the_surprisee(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        sid = await seed(w, stops=stop_dicts(future=True), **surprise_fields())
        await add_legs(w, sid)
        await reveal(w, sid)

        partner = (await w.partner.get(f"{ITINERARIES}/{sid}")).json()
        first, hidden, last = partner["stops"]
        assert hidden["title"] == "Surprise stop ✨"
        for field in (
            "description",
            "notes",
            "place_id",
            "event",
            "venue",
            "booking_url",
            "est_cost",
            "travel_to_next",
        ):
            assert hidden[field] is None, field
        assert hidden["warnings"] == []
        assert hidden["maps"] == {"apple": None, "google": None, "directions_from_previous": None}
        assert first["title"] == "Opening"
        assert first["travel_to_next"] is None
        assert first["maps"]["google"] is not None
        assert last["title"] == "Dessert"
        assert last["maps"]["google"] is not None
        assert last["maps"]["directions_from_previous"] is None
        assert SECRET_STOP not in json.dumps(partner)
        assert "hush" not in json.dumps(partner)
        assert "Ticketmaster" not in json.dumps(partner["attributions"])

        planner = (await w.owner.get(f"{ITINERARIES}/{sid}")).json()
        assert planner["stops"][1]["title"] == SECRET_STOP
        assert planner["stops"][1]["notes"] == "hush"
        assert planner["stops"][1]["venue"] is not None
        assert planner["stops"][0]["travel_to_next"] is not None
        assert planner["stops"][1]["travel_to_next"] is not None
        assert planner["stops"][2]["maps"]["directions_from_previous"] is not None


async def test_a_past_at_stop_time_stop_is_not_redacted(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        sid = await seed(w, stops=stop_dicts(future=False), **surprise_fields())
        await add_legs(w, sid)
        await reveal(w, sid)
        stops = (await w.partner.get(f"{ITINERARIES}/{sid}")).json()["stops"]
        assert stops[1]["title"] == SECRET_STOP
        assert stops[1]["notes"] == "hush"
        assert stops[0]["travel_to_next"] is not None
        assert stops[2]["maps"]["directions_from_previous"] is not None


async def test_with_itinerary_stops_are_never_redacted(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        stops = [{**s, "reveal_policy": "with_itinerary"} for s in stop_dicts(future=True)]
        sid = await seed(w, stops=stops, **surprise_fields())
        await reveal(w, sid)
        shown = (await w.partner.get(f"{ITINERARIES}/{sid}")).json()["stops"]
        assert [s["title"] for s in shown] == ["Opening", SECRET_STOP, "Dessert"]


async def add_idea(w: World, **fields: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "couple_id": w.couple_id,
        "created_by_user_id": w.owner_id,
        "source": "manual",
        "visibility": "shared",
        "title": "Plain idea",
        "pitch": "",
    }
    values.update(fields)
    async with w.app.state.sessionmaker() as session:
        idea = Idea(**values)
        session.add(idea)
        await session.commit()
        return idea.id


async def test_a_surprise_idea_is_visible_only_to_its_creator(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        secret = await add_idea(w, title="Surprise idea", is_surprise=True)
        await add_idea(w, title="Plain idea")

        both = {"Surprise idea", "Plain idea"}
        for client, titles in ((w.owner, both), (w.partner, {"Plain idea"})):
            listed = await client.get(IDEAS, params={"visibility": "shared"})
            assert {i["title"] for i in listed.json()} == titles
            deck = await client.get("/api/v1/deck")
            assert {i["title"] for i in deck.json()["cards"]} == titles
        assert "Surprise idea" not in (await w.partner.get(f"{IDEAS}/lists")).text

        patch = await call(w.partner, "PATCH", f"{IDEAS}/{secret}", {"title": "Hijack"})
        assert patch.status_code == 404
        vote = await call(w.partner, "PUT", f"{IDEAS}/{secret}/vote", {"value": "yes"})
        assert vote.status_code == 404

        mine = await call(w.owner, "PATCH", f"{IDEAS}/{secret}", {"title": "Still mine"})
        assert mine.status_code == 200, mine.text


async def test_a_surprise_idea_never_reaches_the_partners_ai_context(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await add_idea(w, title="Hidden surprise idea", is_surprise=True)
        await add_idea(w, title="Visible idea")
        recipient = uuid.UUID((await profile_ids(w.owner))["owner"])
        async with db_app.state.sessionmaker() as session:
            as_partner = await build_context(session, w.couple_id, w.partner_id, recipient)
            as_owner = await build_context(session, w.couple_id, w.owner_id, recipient)
        assert "Visible idea" in as_partner
        assert "Hidden surprise idea" not in as_partner
        assert "Hidden surprise idea" in as_owner


def warning(index: int | None, message: str) -> dict[str, Any]:

    return {"stop_index": index, "code": "x", "message": message}


async def test_redaction_hides_the_total_and_warnings_that_could_leak_a_hidden_stop(
    db_app: FastAPI,
) -> None:

    async with world(db_app) as w:
        warnings = [
            warning(0, "Opening is closed"),
            warning(1, f"{SECRET_STOP} is closed"),
            warning(None, f"{SECRET_STOP} is pricey"),
        ]

        extra = {"est_total": 123, "warnings": warnings}

        sid = await seed(w, stops=stop_dicts(future=True), **surprise_fields(**extra))

        past = await seed(w, stops=stop_dicts(future=False), **surprise_fields(**extra))

        await reveal(w, sid)

        await reveal(w, past)

        hidden = (await w.partner.get(f"{ITINERARIES}/{sid}")).json()

        assert hidden["est_total"] is None

        assert [x["message"] for x in hidden["warnings"]] == ["Opening is closed"]

        assert SECRET_STOP not in json.dumps(hidden)

        planner = (await w.owner.get(f"{ITINERARIES}/{sid}")).json()

        assert planner["est_total"] == 123

        assert len(planner["warnings"]) == 3

        shown = (await w.partner.get(f"{ITINERARIES}/{past}")).json()

        assert shown["est_total"] == 123

        assert len(shown["warnings"]) == 3


async def test_undoing_a_vote_never_returns_a_surprise_idea_to_the_partner(
    db_app: FastAPI,
) -> None:

    async with world(db_app) as w:
        secret = await add_idea(w, title="Surprise idea", is_surprise=True)

        async with w.app.state.sessionmaker() as session:
            session.add(Vote(idea_id=secret, user_id=w.partner_id, value="yes"))

            await session.commit()

        undo = await call(w.partner, "POST", "/api/v1/deck/undo")

        assert undo.status_code == 204

        assert "Surprise idea" not in undo.text

        async with w.app.state.sessionmaker() as session:
            votes = (await session.execute(select(Vote.idea_id))).scalars().all()

            assert votes == [secret]


async def test_a_hidden_surprise_does_not_lock_the_surprisees_vote(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        idea = await add_idea(w, title="Pottery")
        sid = await seed(w, idea_id=idea, **surprise_fields())
        vote_url = f"{IDEAS}/{idea}/vote"

        # The planner can see the scheduled date, so their vote is frozen.
        locked = await call(w.owner, "PUT", vote_url, {"value": "yes"})
        assert (locked.status_code, locked.json()["error"]["code"]) == (409, "vote_locked")

        # The surprisee can't see it: nothing about it leaks through the vote routes.
        voted = await call(w.partner, "PUT", vote_url, {"value": "yes"})
        assert voted.status_code == 200, voted.text
        undone = await call(w.partner, "POST", "/api/v1/deck/undo")
        assert undone.status_code == 200, undone.text
        again = await call(w.partner, "PUT", vote_url, {"value": "no"})
        assert again.status_code == 200, again.text

        # Once revealed the date is visible to them, so the lock applies to them too.
        await reveal(w, sid)
        frozen = await call(w.partner, "PUT", vote_url, {"value": "yes"})
        assert (frozen.status_code, frozen.json()["error"]["code"]) == (409, "vote_locked")
        assert (await call(w.partner, "POST", "/api/v1/deck/undo")).status_code == 204
