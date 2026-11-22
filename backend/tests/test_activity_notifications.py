"""Partner-activity notifications: reschedule, cancel, reaction and new shared ideas."""

import datetime as dt
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.models import Idea, Job, Notification
from app.services import ideas as idea_service
from tests.authutil import call
from tests.conftest import make_settings
from tests.itinerary_helpers import ITINERARIES, World, seed, set_home, when, world
from tests.test_ideas_generate_api import ai_returns, fixed, idea
from tests.test_notify_hooks import notifications

pytestmark = pytest.mark.db

IDEAS = "/api/v1/ideas"


async def schedule(
    w: World, itinerary_id: uuid.UUID, start: dt.datetime, mode: str = "announce"
) -> None:
    response = await call(
        w.owner,
        "POST",
        f"{ITINERARIES}/{itinerary_id}/schedule",
        {"starts_at": start.isoformat(), "mode": mode},
    )
    assert response.status_code == 200, response.text


async def kinds(w: World, user_id: uuid.UUID) -> list[str]:
    return [n.kind for n in await notifications(w.app, user_id)]


# --- Rescheduled vs announced


async def test_moving_a_scheduled_date_notifies_as_rescheduled(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w, title="Dinner out")
        start = when(14, 18)
        await schedule(w, itinerary_id, start)
        await schedule(w, itinerary_id, start)  # same time: nothing new
        assert await kinds(w, w.partner_id) == ["date_announced"]

        await schedule(w, itinerary_id, start + dt.timedelta(days=1))
        notes = await notifications(w.app, w.partner_id)
        assert [n.kind for n in notes] == ["date_announced", "date_rescheduled"]
        assert notes[1].title == "Your date moved 📅"
        assert notes[1].body.startswith("Dinner out — ")
        assert notes[1].data == {"url": f"/itineraries/{itinerary_id}"}


async def test_surprise_schedule_and_reschedule_stay_silent(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w)
        await schedule(w, itinerary_id, when(14, 18), "surprise")
        await schedule(w, itinerary_id, when(15, 18), "surprise")
        assert await kinds(w, w.partner_id) == []


async def test_switching_an_announced_date_to_a_surprise_stays_silent(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w, title="Dinner out")
        await schedule(w, itinerary_id, when(14, 18))
        await schedule(w, itinerary_id, when(15, 18), "surprise")
        assert await kinds(w, w.partner_id) == ["date_announced"]


async def jobs_for(w: World, kind: str) -> list[Job]:
    async with w.app.state.sessionmaker() as session:
        rows = await session.execute(select(Job).where(Job.kind == kind).order_by(Job.run_at))
        return list(rows.scalars().all())


async def test_patching_the_time_of_an_announced_date_notifies_and_requeues_reminders(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w, title="Dinner out")
        start = when(14, 18)
        await schedule(w, itinerary_id, start)
        moved = start + dt.timedelta(days=2)
        patched = await call(
            w.owner, "PATCH", f"{ITINERARIES}/{itinerary_id}", {"starts_at": moved.isoformat()}
        )
        assert patched.status_code == 200, patched.text
        notes = await notifications(w.app, w.partner_id)
        assert [n.kind for n in notes] == ["date_announced", "date_rescheduled"]
        hours = await jobs_for(w, "reminder.hour_before")
        assert [j.payload["starts_at"] for j in hours] == [
            start.isoformat(),
            moved.isoformat(),
        ]

        # An edit that doesn't move the start tells nobody and adds no jobs.
        again = await call(
            w.owner, "PATCH", f"{ITINERARIES}/{itinerary_id}", {"title": "Dinner in"}
        )
        assert again.status_code == 200, again.text
        assert len(await notifications(w.app, w.partner_id)) == 2
        assert len(await jobs_for(w, "reminder.hour_before")) == 2


async def test_patching_an_unrevealed_surprise_notifies_nobody(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w)
        start = when(14, 18)
        await schedule(w, itinerary_id, start, "surprise")
        patched = await call(
            w.owner,
            "PATCH",
            f"{ITINERARIES}/{itinerary_id}",
            {"starts_at": (start + dt.timedelta(days=1)).isoformat()},
        )
        assert patched.status_code == 200, patched.text
        assert await kinds(w, w.partner_id) == []


async def revealed_surprise(w: World, *, revealed: bool) -> uuid.UUID:
    start = when(14, 18)
    return await seed(
        w,
        title="Secret supper",
        status="scheduled",
        is_surprise=True,
        starts_at=start,
        reveal_at=start,
        revealed_at=when(-1) if revealed else None,
    )


async def test_moving_a_revealed_surprise_notifies_the_partner(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await revealed_surprise(w, revealed=True)
        later = when(16, 18)
        patched = await call(
            w.owner, "PATCH", f"{ITINERARIES}/{itinerary_id}", {"starts_at": later.isoformat()}
        )
        assert patched.status_code == 200, patched.text
        assert await kinds(w, w.partner_id) == ["date_rescheduled"]

        await schedule(w, itinerary_id, when(18, 18), "surprise")
        assert await kinds(w, w.partner_id) == ["date_rescheduled", "date_rescheduled"]
        # Same time again: nothing new.
        await schedule(w, itinerary_id, when(18, 18), "surprise")
        assert len(await kinds(w, w.partner_id)) == 2


async def test_moving_an_unrevealed_surprise_tells_nobody(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await revealed_surprise(w, revealed=False)
        patched = await call(
            w.owner,
            "PATCH",
            f"{ITINERARIES}/{itinerary_id}",
            {"starts_at": when(16, 18).isoformat()},
        )
        assert patched.status_code == 200, patched.text
        await schedule(w, itinerary_id, when(18, 18), "surprise")
        assert await kinds(w, w.partner_id) == []


# --- Cancelled


async def test_cancelling_an_announced_date_notifies_the_partner(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w, title="Dinner out")
        await schedule(w, itinerary_id, when(14, 18))
        response = await call(w.owner, "POST", f"{ITINERARIES}/{itinerary_id}/cancel")
        assert response.status_code == 200, response.text
        note = (await notifications(w.app, w.partner_id))[-1]
        assert (note.kind, note.title, note.body) == (
            "date_cancelled",
            "Date cancelled",
            "Dinner out",
        )
        assert note.data == {"url": f"/itineraries/{itinerary_id}"}
        assert await notifications(w.app, w.owner_id) == []

        again = await call(w.owner, "POST", f"{ITINERARIES}/{itinerary_id}/cancel")
        assert again.status_code == 200
        assert (await kinds(w, w.partner_id)).count("date_cancelled") == 1


async def test_cancelling_a_surprise_or_a_draft_tells_nobody(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        surprise = await seed(w)
        await schedule(w, surprise, when(14, 18), "surprise")
        draft = await seed(w)
        for itinerary_id in (surprise, draft):
            response = await call(w.owner, "POST", f"{ITINERARIES}/{itinerary_id}/cancel")
            assert response.status_code == 200, response.text
        assert await kinds(w, w.partner_id) == []


async def test_cancelling_a_revealed_surprise_notifies_the_partner(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w, title="Dinner out")
        await schedule(w, itinerary_id, when(14, 18), "surprise")
        revealed = await call(w.owner, "POST", f"{ITINERARIES}/{itinerary_id}/reveal")
        assert revealed.status_code == 200, revealed.text
        response = await call(w.owner, "POST", f"{ITINERARIES}/{itinerary_id}/cancel")
        assert response.status_code == 200, response.text
        assert "date_cancelled" in await kinds(w, w.partner_id)


# --- Reaction


async def react(w: World, itinerary_id: uuid.UUID, **body: Any) -> None:
    response = await call(w.partner, "POST", f"{ITINERARIES}/{itinerary_id}/react", body)
    assert response.status_code == 200, response.text


async def test_reaction_notifies_the_planner(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w, title="Dinner out")
        await schedule(w, itinerary_id, when(14, 18))
        await react(w, itinerary_id, kind="heart")
        await react(w, itinerary_id, kind="move", note="Friday instead?")
        first, second = await notifications(w.app, w.owner_id)
        assert first.kind == second.kind == "partner_reaction"
        assert first.title == "❤️ Partner loved the plan"
        assert first.body == "Dinner out"
        assert second.title == "Partner asked to move the date"
        assert second.body == "Friday instead?"
        assert second.data == {"url": f"/itineraries/{itinerary_id}"}
        assert await kinds(w, w.partner_id) == ["date_announced"]


# --- Ideas added


async def ideas_added(w: World, user_id: uuid.UUID) -> list[Notification]:
    return [n for n in await notifications(w.app, user_id) if n.kind == "ideas_added"]


async def private_idea(w: World, title: str, **fields: Any) -> str:
    values: dict[str, Any] = {
        "couple_id": w.couple_id,
        "created_by_user_id": w.owner_id,
        "source": "manual",
        "visibility": "private",
        "title": title,
        "pitch": "",
    }
    values.update(fields)
    async with w.app.state.sessionmaker() as session:
        row = Idea(**values)
        session.add(row)
        await session.commit()
        return str(row.id)


async def test_sharing_ideas_notifies_the_partner_once_an_hour(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        first = await private_idea(w, "One")
        second = await private_idea(w, "Two")
        assert (await call(w.owner, "POST", f"{IDEAS}/{first}/share")).status_code == 200
        assert (await call(w.owner, "POST", f"{IDEAS}/{first}/share")).status_code == 200
        assert (await call(w.owner, "POST", f"{IDEAS}/{second}/share")).status_code == 200
        (note,) = await ideas_added(w, w.partner_id)
        assert note.title == "New date ideas to swipe 💡"
        assert note.body == "1 new idea in your deck."
        assert note.data == {"url": "/ideas"}
        assert await ideas_added(w, w.owner_id) == []


async def test_a_surprise_idea_is_never_announced(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        secret = await private_idea(w, "Secret", is_surprise=True)
        assert (await call(w.owner, "POST", f"{IDEAS}/{secret}/share")).status_code == 200
        assert await ideas_added(w, w.partner_id) == []


async def test_ideas_added_counts_and_buckets_by_the_hour(
    db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with world(db_app) as w:
        settings = make_settings()
        hour = dt.datetime(2030, 1, 1, 10, 5, tzinfo=dt.UTC)
        monkeypatch.setattr(idea_service, "now", lambda: hour)
        async with w.app.state.sessionmaker() as session:
            await idea_service.notify_ideas_added(session, w.couple_id, w.owner_id, 3, settings)
            await idea_service.notify_ideas_added(session, w.couple_id, w.owner_id, 2, settings)
            await session.commit()
        (note,) = await ideas_added(w, w.partner_id)
        assert note.body == "3 new ideas in your deck."

        monkeypatch.setattr(idea_service, "now", lambda: hour + dt.timedelta(hours=1))
        async with w.app.state.sessionmaker() as session:
            await idea_service.notify_ideas_added(session, w.couple_id, w.owner_id, 2, settings)
            await session.commit()
        assert len(await ideas_added(w, w.partner_id)) == 2


async def test_generating_shared_ideas_notifies_the_partner_but_private_ones_do_not(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        await set_home(w)
        private = ("Pottery", "Hike", "Jazz", "Museum", "Pie")
        shared = ("Kayak", "Opera", "Bowling", "Tapas", "Sunrise")
        async with ai_returns(db_app, fixed(*(idea(t) for t in private))):
            plan = await call(w.owner, "POST", f"{IDEAS}/generate", {"mode": "plan", "count": 5})
            assert plan.status_code == 200, plan.text
        assert await ideas_added(w, w.partner_id) == []

        async with ai_returns(db_app, fixed(*(idea(t) for t in shared))):
            match = await call(w.owner, "POST", f"{IDEAS}/generate", {"mode": "match", "count": 5})
            assert match.status_code == 200, match.text
        (note,) = await ideas_added(w, w.partner_id)
        assert note.body == f"{len(match.json()['ideas'])} new ideas in your deck."
