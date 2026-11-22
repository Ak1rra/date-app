"""The weekly plan nudge: who gets it, once a week, through notify."""

import datetime as dt

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.models import Job, Notification
from app.worker import handlers
from tests.itinerary_helpers import World, seed, when, world

pytestmark = pytest.mark.db


async def run_nudge(w: World) -> None:
    async with w.app.state.sessionmaker() as session:
        await handlers.nudge_weekly(session, {})
        await session.commit()


async def nudges(w: World) -> list[Notification]:
    async with w.app.state.sessionmaker() as session:
        rows = await session.execute(select(Notification).where(Notification.kind == "plan_nudge"))
        return list(rows.scalars().all())


def test_the_nudge_job_is_registered() -> None:
    from app.worker.jobs import registered_kinds

    assert "nudge.weekly" in registered_kinds()


async def test_it_nudges_a_couple_with_nothing_scheduled(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await seed(w, status="draft")
        await seed(w, status="scheduled", starts_at=when(20), announced_at=when(-1))  # too far
        await run_nudge(w)
        notes = await nudges(w)
        assert {n.user_id for n in notes} == {w.owner_id, w.partner_id}
        for note in notes:
            assert note.title == "Nothing on the calendar 👀"
            assert note.body == "The next two weeks are wide open. Want me to plan something fun?"
            assert note.data == {"url": "/plan"}
            assert note.couple_id == w.couple_id
        # It queued the next day's run.
        async with w.app.state.sessionmaker() as session:
            queued = (await session.execute(select(Job).where(Job.kind == "nudge.weekly"))).all()
        assert len(queued) == 1


async def test_it_skips_a_couple_with_a_date_in_ten_days(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await seed(w, status="scheduled", starts_at=when(10), announced_at=when(-1))
        await run_nudge(w)
        assert await nudges(w) == []


async def test_it_does_not_nudge_twice_within_seven_days(
    db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with world(db_app) as w:
        await run_nudge(w)
        assert len(await nudges(w)) == 2
        tomorrow = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
        monkeypatch.setattr(handlers, "_utcnow", lambda: tomorrow)
        await run_nudge(w)
        assert len(await nudges(w)) == 2
        later = dt.datetime.now(dt.UTC) + dt.timedelta(days=8)
        monkeypatch.setattr(handlers, "_utcnow", lambda: later)
        await run_nudge(w)
        assert len(await nudges(w)) == 4


async def test_the_next_run_is_tomorrow_even_if_the_job_crosses_midnight(
    db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = iter([dt.datetime(2099, 3, 4, 23, 59, 59, tzinfo=dt.UTC)])
    after_midnight = dt.datetime(2099, 3, 5, 0, 0, 1, tzinfo=dt.UTC)
    monkeypatch.setattr(handlers, "_utcnow", lambda: next(clock, after_midnight))
    async with world(db_app) as w:
        await run_nudge(w)
        async with w.app.state.sessionmaker() as session:
            keys = (
                (await session.execute(select(Job.dedupe_key).where(Job.kind == "nudge.weekly")))
                .scalars()
                .all()
            )
        assert list(keys) == ["nudge.weekly:2099-03-05"]
