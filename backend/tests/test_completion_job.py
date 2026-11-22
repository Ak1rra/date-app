"""M7: the job that completes a date after it ends, and the rating reveal job."""

import datetime as dt
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from app.errors import ApiError
from app.models import Itinerary, Job, Notification
from app.schemas.itineraries import ItineraryPatch
from app.services.itineraries import patch_itinerary
from app.worker import handlers
from tests.authutil import call
from tests.itinerary_helpers import ITINERARIES, World, fake_providers, seed, when, world

pytestmark = pytest.mark.db


async def seed_scheduled(w: World, **fields: Any) -> tuple[uuid.UUID, dt.datetime]:
    values: dict[str, Any] = {
        "title": "Rooftop dinner",
        "status": "scheduled",
        "starts_at": when(-2, 18),
        "ends_at": when(-2, 21),
        "announced_at": when(-5, 12),
    }
    values.update(fields)
    end = values["ends_at"] or values["starts_at"] + dt.timedelta(hours=3)
    return await seed(w, **values), end


async def run_complete(
    app: FastAPI, itinerary_id: uuid.UUID, end: dt.datetime
) -> list[tuple[Any, ...]]:
    """Run the handler; returns the realtime events it queued."""
    async with app.state.sessionmaker() as session:
        await handlers.itinerary_complete(
            session, {"itinerary_id": str(itinerary_id), "end": end.isoformat()}
        )
        events: list[tuple[Any, ...]] = list(session.sync_session.info.get("realtime", []))
        await session.commit()
        return events


async def load(app: FastAPI, itinerary_id: uuid.UUID) -> Itinerary:
    async with app.state.sessionmaker() as session:
        itinerary = await session.get(Itinerary, itinerary_id)
        assert itinerary is not None
        return itinerary


async def jobs(app: FastAPI, kind: str) -> list[Job]:
    async with app.state.sessionmaker() as session:
        return list(
            (await session.execute(select(Job).where(Job.kind == kind).order_by(Job.created_at)))
            .scalars()
            .all()
        )


async def prompts(app: FastAPI, user_id: uuid.UUID) -> list[Notification]:
    async with app.state.sessionmaker() as session:
        return list(
            (
                await session.execute(
                    select(Notification).where(
                        Notification.user_id == user_id, Notification.kind == "rate_prompt"
                    )
                )
            )
            .scalars()
            .all()
        )


async def test_scheduling_queues_a_completion_job_at_the_end_time(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w)
        start = when(14, 18)
        response = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            {"starts_at": start.isoformat(), "mode": "announce"},
        )
        assert response.status_code == 200, response.text
        end = start + dt.timedelta(hours=3)  # no end time was given
        queued = await jobs(w.app, "itinerary.complete")
        assert len(queued) == 1
        assert queued[0].run_at == end
        assert queued[0].payload == {"itinerary_id": str(itinerary_id), "end": end.isoformat()}

        later = start + dt.timedelta(days=1)
        response = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            {"starts_at": later.isoformat(), "mode": "announce"},
        )
        assert response.status_code == 200, response.text
        assert len(await jobs(w.app, "itinerary.complete")) == 2


async def test_completion_marks_completed_prompts_both_and_queues_the_reveal(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id, end = await seed_scheduled(w)
        events = await run_complete(w.app, itinerary_id, end)

        itinerary = await load(w.app, itinerary_id)
        assert itinerary.status == "completed"
        for user_id in (w.owner_id, w.partner_id):
            rows = await prompts(w.app, user_id)
            assert len(rows) == 1
            assert rows[0].title == "How was your date? ⭐"
            assert rows[0].body == "Tap to rate Rooftop dinner."
            assert rows[0].data == {"url": f"/itineraries/{itinerary_id}?rate=1"}
        reveal = await jobs(w.app, "ratings.reveal")
        assert len(reveal) == 1
        assert reveal[0].run_at == end + dt.timedelta(days=7)
        assert reveal[0].payload == {"itinerary_id": str(itinerary_id)}
        assert "date.completed" in {event[1] for event in events}

        await run_complete(w.app, itinerary_id, end)  # a repeat changes nothing
        assert len(await prompts(w.app, w.owner_id)) == 1
        assert len(await jobs(w.app, "ratings.reveal")) == 1


async def test_stale_completion_payload_is_a_no_op(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id, end = await seed_scheduled(w)
        await run_complete(w.app, itinerary_id, end - dt.timedelta(hours=1))
        assert (await load(w.app, itinerary_id)).status == "scheduled"
        assert await jobs(w.app, "ratings.reveal") == []
        assert await prompts(w.app, w.owner_id) == []

        cancelled_id, cancelled_end = await seed_scheduled(w, status="cancelled")
        await run_complete(w.app, cancelled_id, cancelled_end)
        assert (await load(w.app, cancelled_id)).status == "cancelled"

        async with w.app.state.sessionmaker() as session:  # junk payloads are ignored
            await handlers.itinerary_complete(session, {"itinerary_id": "nope"})
            await handlers.itinerary_complete(
                session, {"itinerary_id": str(uuid.uuid4()), "end": end.isoformat()}
            )


async def test_completing_a_surprise_reveals_it(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id, end = await seed_scheduled(
            w, is_surprise=True, announced_at=None, reveal_at=when(-2, 18)
        )
        assert (await load(w.app, itinerary_id)).revealed_at is None
        await run_complete(w.app, itinerary_id, end)
        itinerary = await load(w.app, itinerary_id)
        assert itinerary.status == "completed"
        assert itinerary.revealed_at is not None


async def test_end_defaults_to_three_hours_after_the_start(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id, end = await seed_scheduled(w, ends_at=None)
        await run_complete(w.app, itinerary_id, end)
        assert (await load(w.app, itinerary_id)).status == "completed"


async def test_ratings_reveal_job_emits_only_for_a_completed_date(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        done_id = await seed(w, status="completed", starts_at=when(-9, 18))
        open_id = await seed(w, status="scheduled", starts_at=when(5, 18))
        for itinerary_id, expected in ((done_id, True), (open_id, False)):
            async with w.app.state.sessionmaker() as session:
                await handlers.ratings_reveal(session, {"itinerary_id": str(itinerary_id)})
                events = list(session.sync_session.info.get("realtime", []))
                await session.rollback()
            if expected:
                assert [(e[0], e[1], e[2]) for e in events] == [
                    (w.couple_id, "rating.revealed", {"itinerary_id": str(itinerary_id)})
                ]
            else:
                assert events == []


async def test_patching_a_scheduled_dates_times_queues_a_new_completion_job(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id, _ = await seed_scheduled(
            w, starts_at=when(5, 18), ends_at=when(5, 21), announced_at=when(1, 12)
        )
        new_start = when(8, 18)
        response = await call(
            w.owner,
            "PATCH",
            f"{ITINERARIES}/{itinerary_id}",
            {
                "starts_at": new_start.isoformat(),
                "ends_at": (new_start + dt.timedelta(hours=2)).isoformat(),
            },
        )
        assert response.status_code == 200, response.text
        queued = await jobs(w.app, "itinerary.complete")
        assert len(queued) == 1
        end = new_start + dt.timedelta(hours=2)
        assert queued[0].run_at == end
        assert queued[0].payload == {"itinerary_id": str(itinerary_id), "end": end.isoformat()}

        await run_complete(w.app, itinerary_id, end)
        assert (await load(w.app, itinerary_id)).status == "completed"


@pytest.mark.parametrize("status", ["completed", "cancelled"])
async def test_a_finished_dates_times_cannot_be_patched(db_app: FastAPI, status: str) -> None:
    async with world(db_app) as w:
        itinerary_id, _ = await seed_scheduled(w, status=status)
        response = await call(
            w.owner,
            "PATCH",
            f"{ITINERARIES}/{itinerary_id}",
            {"starts_at": when(-10, 18).isoformat(), "ends_at": when(-10, 21).isoformat()},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_editable"
        assert (await load(w.app, itinerary_id)).starts_at == when(-2, 18)

        ok = await call(w.owner, "PATCH", f"{ITINERARIES}/{itinerary_id}", {"title": "Renamed"})
        assert ok.status_code == 200, ok.text


async def test_rescheduling_back_to_an_earlier_end_rearms_its_completion_job(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w)
        first = when(14, 18)
        second = first + dt.timedelta(days=1)

        async def reschedule(start: dt.datetime) -> None:
            response = await call(
                w.owner,
                "POST",
                f"{ITINERARIES}/{itinerary_id}/schedule",
                {"starts_at": start.isoformat(), "mode": "announce"},
            )
            assert response.status_code == 200, response.text

        await reschedule(first)
        await reschedule(second)
        first_end = first + dt.timedelta(hours=3)
        async with w.app.state.sessionmaker() as session:
            # The first job ran after the move: a no-op, then marked done.
            await handlers.itinerary_complete(
                session, {"itinerary_id": str(itinerary_id), "end": first_end.isoformat()}
            )
            await session.execute(
                update(Job)
                .where(Job.dedupe_key == f"complete:{itinerary_id}:{first_end.isoformat()}")
                .values(status="done", finished_at=dt.datetime.now(dt.UTC), attempts=1)
            )
            await session.commit()
        assert (await load(w.app, itinerary_id)).status == "scheduled"

        await reschedule(first)
        rows = {job.payload["end"]: job for job in await jobs(w.app, "itinerary.complete")}
        assert len(rows) == 2
        rearmed = rows[first_end.isoformat()]
        assert rearmed.status == "queued"
        assert rearmed.attempts == 0 and rearmed.finished_at is None
        assert rearmed.run_at == first_end
        assert rows[(second + dt.timedelta(hours=3)).isoformat()].status == "queued"

        await run_complete(w.app, itinerary_id, first_end)
        assert (await load(w.app, itinerary_id)).status == "completed"


async def test_patch_rechecks_the_status_after_locking_the_row(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id, _ = await seed_scheduled(
            w, starts_at=when(5, 18), ends_at=when(5, 21), announced_at=when(1, 12)
        )
        async with w.app.state.sessionmaker() as stale_session:
            stale = await stale_session.get(Itinerary, itinerary_id)
            assert stale is not None and stale.status == "scheduled"
            async with w.app.state.sessionmaker() as other:  # the date completes meanwhile
                done = await other.get(Itinerary, itinerary_id)
                assert done is not None
                done.status = "completed"
                await other.commit()
            moved = when(1, 18)
            with pytest.raises(ApiError) as caught:
                await patch_itinerary(
                    stale_session,
                    stale,
                    ItineraryPatch(starts_at=moved, ends_at=moved + dt.timedelta(hours=2)),
                    fake_providers(),
                    w.app.state.settings,
                )
            assert caught.value.code == "not_editable"
        assert (await load(w.app, itinerary_id)).starts_at == when(5, 18)
