"""Reminder and checklist-due jobs: scheduling, stale payloads, wording and quiet hours."""

import datetime as dt
import uuid
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from app.models import ChecklistItem, Couple, Itinerary, Job, Notification, PushSubscription, User
from app.services import reminders
from app.worker import handlers
from app.worker.jobs import registered_kinds
from tests.authutil import call
from tests.conftest import make_settings
from tests.itinerary_helpers import ITINERARIES, World, fake_providers, seed, when, world

pytestmark = pytest.mark.db

SETTINGS = make_settings()


async def jobs(w: World, kind: str) -> list[Job]:
    async with w.app.state.sessionmaker() as session:
        rows = await session.execute(select(Job).where(Job.kind == kind).order_by(Job.run_at))
        return list(rows.scalars().all())


async def notes(w: World, user_id: uuid.UUID, kind: str | None = None) -> list[Notification]:
    async with w.app.state.sessionmaker() as session:
        query = select(Notification).where(Notification.user_id == user_id)
        if kind is not None:
            query = query.where(Notification.kind == kind)
        return list((await session.execute(query.order_by(Notification.created_at))).scalars())


async def run(
    w: World, which: reminders.Which, itinerary_id: uuid.UUID, starts: dt.datetime
) -> None:
    payload = {
        "itinerary_id": str(itinerary_id),
        "starts_at": starts.astimezone(dt.UTC).isoformat(),
    }
    async with w.app.state.sessionmaker() as session:
        await reminders.send_reminder(
            session, which, payload, SETTINGS, lambda _couple: fake_providers()
        )
        await session.commit()


async def set_timezone(w: World, name: str) -> None:
    async with w.app.state.sessionmaker() as session:
        couple = await session.get(Couple, w.couple_id)
        assert couple is not None
        couple.timezone = name
        await session.commit()


def test_handlers_are_registered() -> None:
    assert {"reminder.day_before", "reminder.hour_before", "checklist.due"} <= registered_kinds()
    assert handlers.reminder_day_before and handlers.reminder_hour_before and handlers.checklist_due


async def test_schedule_enqueues_both_reminders_at_the_right_times(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_timezone(w, "America/New_York")
        itinerary_id = await seed(w, title="Dinner")
        start = when(10, 23)
        response = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            {"starts_at": start.isoformat(), "mode": "announce"},
        )
        assert response.status_code == 200, response.text

        (day,) = await jobs(w, "reminder.day_before")
        (hour,) = await jobs(w, "reminder.hour_before")
        zone = ZoneInfo("America/New_York")
        local = start.astimezone(zone)
        expected = dt.datetime.combine(
            local.date() - dt.timedelta(days=1), dt.time(18), tzinfo=zone
        )
        assert day.run_at == expected
        assert hour.run_at == start - dt.timedelta(hours=1)
        assert day.payload == {"itinerary_id": str(itinerary_id), "starts_at": start.isoformat()}

        # Scheduling the same time again adds nothing.
        await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            {"starts_at": start.isoformat(), "mode": "announce"},
        )
        assert len(await jobs(w, "reminder.day_before")) == 1
        assert len(await jobs(w, "reminder.hour_before")) == 1


async def test_reminders_in_the_past_are_not_enqueued(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w)
        soon = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)
        response = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            {"starts_at": soon.isoformat(), "mode": "announce"},
        )
        assert response.status_code == 200, response.text
        assert await jobs(w, "reminder.day_before") == []
        assert await jobs(w, "reminder.hour_before") == []


async def test_reminder_wording_for_both_members(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_timezone(w, "UTC")
        start = when(10, 19)
        itinerary_id = await seed(
            w,
            title="Dinner out",
            status="scheduled",
            announced_at=when(-1),
            starts_at=start,
            warnings=[{"stop_index": 0, "code": "closed", "message": "Rain is likely"}],
        )
        for text in ("wine", "flowers", "tickets", "umbrella"):
            added = await call(
                w.owner,
                "POST",
                f"{ITINERARIES}/{itinerary_id}/checklist",
                {"text": text, "kind": "bring"},
            )
            assert added.status_code == 201, added.text
            if text == "wine":
                done = await call(
                    w.owner,
                    "PATCH",
                    f"{ITINERARIES}/{itinerary_id}/checklist/{added.json()['id']}",
                    {"done": True},
                )
                assert done.status_code == 200

        await run(w, "day", itinerary_id, start)
        for user_id in (w.owner_id, w.partner_id):
            (note,) = await notes(w, user_id, "reminder_day_before")
            assert note.body == (
                "Dinner out tomorrow at 7:00 PM. Bring: flowers, tickets, umbrella."
                " Heads-up: Rain is likely."
            )
            assert note.data["url"] == f"/itineraries/{itinerary_id}"

        await run(w, "hour", itinerary_id, start)
        (note,) = await notes(w, w.partner_id, "reminder_hour_before")
        assert note.body == "Dinner out starts at 7:00 PM."

        # Running a job again is a no-op.
        await run(w, "hour", itinerary_id, start)
        assert len(await notes(w, w.partner_id, "reminder_hour_before")) == 1


async def test_day_before_lists_up_to_three_warnings(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_timezone(w, "UTC")
        start = when(10, 19)
        fields: dict[str, Any] = {
            "status": "scheduled",
            "announced_at": when(-1),
            "starts_at": start,
        }
        pair = [{"code": "a", "message": "Rain is likely"}, {"code": "b", "message": "Closed"}]
        two = await seed(w, title="Two", warnings=pair, **fields)
        many = await seed(
            w,
            title="Many",
            warnings=[{"code": "x", "message": f"W{n}"} for n in range(5)],
            **fields,
        )
        await run(w, "day", two, start)
        await run(w, "day", many, start)
        bodies = {n.body for n in await notes(w, w.partner_id, "reminder_day_before")}
        assert "Two tomorrow at 7:00 PM. Heads-up: Rain is likely; Closed." in bodies
        assert "Many tomorrow at 7:00 PM. Heads-up: W0; W1; W2. (+2 more)" in bodies


async def test_day_before_lists_up_to_three_bring_items(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_timezone(w, "UTC")
        start = when(10, 19)
        itinerary_id = await seed(
            w, title="Picnic", status="scheduled", announced_at=when(-1), starts_at=start
        )
        for text in ("a", "b", "c", "d", "e"):
            added = await call(
                w.owner,
                "POST",
                f"{ITINERARIES}/{itinerary_id}/checklist",
                {"text": text, "kind": "bring"},
            )
            assert added.status_code == 201, added.text
        await run(w, "day", itinerary_id, start)
        (note,) = await notes(w, w.partner_id, "reminder_day_before")
        assert note.body == "Picnic tomorrow at 7:00 PM. Bring: a, b, c. (+2 more)"


async def test_delayed_reminders_are_skipped_once_their_wording_is_wrong(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        fields: dict[str, Any] = {"status": "scheduled", "announced_at": when(-1)}
        now = dt.datetime.now(dt.UTC)
        soon = now + dt.timedelta(minutes=90)  # inside two hours
        gone = now - dt.timedelta(minutes=5)  # already started
        near = await seed(w, title="Near", starts_at=soon, **fields)
        past = await seed(w, title="Past", starts_at=gone, **fields)

        await run(w, "day", near, soon)
        assert await notes(w, w.partner_id, "reminder_day_before") == []
        await run(w, "hour", near, soon)
        assert len(await notes(w, w.partner_id, "reminder_hour_before")) == 1

        await run(w, "day", past, gone)
        await run(w, "hour", past, gone)
        assert len(await notes(w, w.partner_id)) == 1  # only Near's hour-before


async def test_hidden_surprise_reminders_are_generic_for_the_surprisee(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        start = when(10, 19)
        title = "Rooftop Secret Supper"
        itinerary_id = await seed(
            w,
            title=title,
            status="scheduled",
            is_surprise=True,
            starts_at=start,
            reveal_at=start,
            stops=[{"title": "Hidden Venue Name", "starts_at": start, "ends_at": start}],
        )
        await run(w, "day", itinerary_id, start)
        await run(w, "hour", itinerary_id, start)

        day = (await notes(w, w.partner_id, "reminder_day_before"))[0]
        hour = (await notes(w, w.partner_id, "reminder_hour_before"))[0]
        assert (day.title, day.body) == (
            "Your surprise date is tomorrow ✨",
            "Get ready — hints are on the way.",
        )
        assert (hour.title, hour.body) == (
            "Your surprise date is in 1 hour! ✨",
            "Time to get ready.",
        )
        for note in (day, hour):
            assert note.data["url"] == f"/surprise/{itinerary_id}"
            blob = f"{note.title} {note.body} {note.data}"
            assert title not in blob and "Hidden Venue" not in blob
        # The planner knows the secret, so gets the real text.
        assert title in (await notes(w, w.owner_id, "reminder_hour_before"))[0].body


async def test_revealed_surprise_reminds_the_surprisee_normally(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        start = when(10, 19)
        itinerary_id = await seed(
            w,
            title="Supper",
            status="scheduled",
            is_surprise=True,
            starts_at=start,
            reveal_at=start,
            revealed_at=when(-1),
        )
        await run(w, "hour", itinerary_id, start)
        assert "Supper" in (await notes(w, w.partner_id, "reminder_hour_before"))[0].body


async def test_stale_reminders_do_nothing(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        start = when(10, 19)
        itinerary_id = await seed(w, status="scheduled", announced_at=when(-1), starts_at=start)
        # The job was made for another time.
        await run(w, "day", itinerary_id, start + dt.timedelta(days=1))
        await run(w, "hour", itinerary_id, start + dt.timedelta(hours=1))
        # Garbage payloads.
        async with w.app.state.sessionmaker() as session:
            for payload in ({}, {"itinerary_id": "x"}, {"itinerary_id": str(itinerary_id)}):
                await reminders.send_reminder(
                    session, "hour", payload, SETTINGS, lambda _couple: fake_providers()
                )
            await session.commit()
        assert await notes(w, w.partner_id) == []

        async with w.app.state.sessionmaker() as session:
            itinerary = await session.get(Itinerary, itinerary_id)
            assert itinerary is not None
            itinerary.status = "cancelled"
            await session.commit()
        await run(w, "hour", itinerary_id, start)
        assert await notes(w, w.partner_id) == []
        assert await notes(w, w.owner_id) == []


async def set_quiet(w: World, user_id: uuid.UUID, now: dt.datetime) -> None:
    """Quiet hours (UTC) from an hour ago to an hour from now."""
    async with w.app.state.sessionmaker() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.timezone = "UTC"
        user.notification_settings = {
            "quiet_start": f"{now - dt.timedelta(hours=1):%H:%M}",
            "quiet_end": f"{now + dt.timedelta(hours=1):%H:%M}",
        }
        await session.commit()


async def test_hour_before_ignores_quiet_hours_only_when_the_date_starts_in_them(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        now = dt.datetime.now(dt.UTC)
        await set_quiet(w, w.partner_id, now)
        inside = when(10, 0).replace(hour=now.hour)  # the clock hour of now: in quiet hours
        outside = when(10, 0).replace(hour=(now.hour + 6) % 24)
        fields: dict[str, Any] = {"status": "scheduled", "announced_at": when(-1)}
        late = await seed(w, title="Late", starts_at=inside, **fields)
        early = await seed(w, title="Early", starts_at=outside, **fields)
        await run(w, "hour", late, inside)
        await run(w, "hour", early, outside)
        await run(w, "day", early, outside)

        by_body = {n.body: n for n in await notes(w, w.partner_id)}
        exempt = by_body[f"Late starts at {reminders.clock_label(inside)}."]
        deferred = by_body[f"Early starts at {reminders.clock_label(outside)}."]
        assert exempt.deliver_after - exempt.created_at < dt.timedelta(minutes=1)
        assert deferred.deliver_after > deferred.created_at + dt.timedelta(minutes=30)
        day = next(n for n in by_body.values() if n.kind == "reminder_day_before")
        assert day.deliver_after > day.created_at + dt.timedelta(minutes=30)


# --- Checklist due


async def add_item(w: World, itinerary_id: uuid.UUID, **body: Any) -> str:
    response = await call(
        w.owner,
        "POST",
        f"{ITINERARIES}/{itinerary_id}/checklist",
        {"text": "Book table", "kind": "reserve", **body},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def run_due(w: World, item_id: str, due: dt.datetime) -> None:
    payload = {"item_id": item_id, "due_at": due.astimezone(dt.UTC).isoformat()}
    async with w.app.state.sessionmaker() as session:
        await reminders.send_checklist_due(session, payload, SETTINGS)
        await session.commit()


async def test_checklist_due_is_enqueued_a_day_ahead_and_only_if_future(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w, status="scheduled", starts_at=when(10, 19))
        due = when(5, 12)
        item_id = await add_item(w, itinerary_id, due_at=due.isoformat())
        (job,) = await jobs(w, "checklist.due")
        assert job.run_at == due - dt.timedelta(hours=24)
        assert job.payload == {"item_id": item_id, "due_at": due.isoformat()}

        soon = dt.datetime.now(dt.UTC) + dt.timedelta(hours=5)
        await add_item(w, itinerary_id, due_at=soon.isoformat())  # its nudge time has passed
        await add_item(w, itinerary_id)  # no due time
        assert len(await jobs(w, "checklist.due")) == 1

        later = when(6, 12)
        patched = await call(
            w.owner,
            "PATCH",
            f"{ITINERARIES}/{itinerary_id}/checklist/{item_id}",
            {"due_at": later.isoformat()},
        )
        assert patched.status_code == 200, patched.text
        assert len(await jobs(w, "checklist.due")) == 2


async def test_scheduling_enqueues_due_jobs_for_items_added_earlier(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w, title="Planned by AI")
        due = when(5, 12)
        async with w.app.state.sessionmaker() as session:
            session.add_all(
                [
                    ChecklistItem(
                        itinerary_id=itinerary_id,
                        position=0,
                        text="Book",
                        kind="reserve",
                        due_at=due,
                    ),
                    ChecklistItem(
                        itinerary_id=itinerary_id,
                        position=1,
                        text="Done already",
                        kind="buy",
                        due_at=due,
                        done_at=when(-1),
                    ),
                    ChecklistItem(itinerary_id=itinerary_id, position=2, text="No due", kind="buy"),
                ]
            )
            await session.commit()
        assert await jobs(w, "checklist.due") == []  # a draft has no reminders yet

        body = {"starts_at": when(10, 19).isoformat(), "mode": "announce"}
        for _ in range(2):
            response = await call(w.owner, "POST", f"{ITINERARIES}/{itinerary_id}/schedule", body)
            assert response.status_code == 200, response.text
        (job,) = await jobs(w, "checklist.due")
        assert job.run_at == due - dt.timedelta(hours=24)
        assert job.payload["due_at"] == due.isoformat()


async def test_unticking_an_item_ticked_before_scheduling_enqueues_its_due_job(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w, title="Planned by AI")
        due = when(5, 12)
        async with w.app.state.sessionmaker() as session:
            item = ChecklistItem(
                itinerary_id=itinerary_id,
                position=0,
                text="Book",
                kind="reserve",
                due_at=due,
                done_at=when(-1),
            )
            session.add(item)
            await session.commit()
            item_id = item.id
        body = {"starts_at": when(10, 19).isoformat(), "mode": "announce"}
        response = await call(w.owner, "POST", f"{ITINERARIES}/{itinerary_id}/schedule", body)
        assert response.status_code == 200, response.text
        assert await jobs(w, "checklist.due") == []  # ticked, so skipped

        url = f"{ITINERARIES}/{itinerary_id}/checklist/{item_id}"
        for _ in range(2):  # a second untick adds nothing
            assert (await call(w.owner, "PATCH", url, {"done": False})).status_code == 200
        (job,) = await jobs(w, "checklist.due")
        assert job.run_at == due - dt.timedelta(hours=24)


async def test_checklist_due_notifies_the_planner_only(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_timezone(w, "UTC")
        itinerary_id = await seed(w, status="scheduled", starts_at=when(10, 19))
        due = when(5, 12)
        item_id = await add_item(w, itinerary_id, due_at=due.isoformat())
        await run_due(w, item_id, due)
        await run_due(w, item_id, due)  # deduped
        (note,) = await notes(w, w.owner_id, "checklist_due")
        assert note.title == "Checklist reminder 📝"
        assert note.body == f"Book table — due {reminders.local_label(due)}"
        assert note.data["url"] == f"/itineraries/{itinerary_id}"
        assert await notes(w, w.partner_id) == []


async def test_checklist_due_skips_ticked_moved_and_deleted_items(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w, status="scheduled", starts_at=when(10, 19))
        due = when(5, 12)
        ticked = await add_item(w, itinerary_id, due_at=due.isoformat())
        moved = await add_item(w, itinerary_id, due_at=due.isoformat())
        gone = await add_item(w, itinerary_id, due_at=due.isoformat())
        base = f"{ITINERARIES}/{itinerary_id}/checklist"
        assert (await call(w.owner, "PATCH", f"{base}/{ticked}", {"done": True})).status_code == 200
        later = when(6, 12)
        assert (
            await call(w.owner, "PATCH", f"{base}/{moved}", {"due_at": later.isoformat()})
        ).status_code == 200
        assert (await call(w.owner, "DELETE", f"{base}/{gone}")).status_code == 204

        for item_id in (ticked, moved, gone, str(uuid.uuid4()), "nope"):
            await run_due(w, item_id, due)
        assert await notes(w, w.owner_id) == []

        # The moved item still fires for its new time.
        await run_due(w, moved, later)
        assert len(await notes(w, w.owner_id, "checklist_due")) == 1


# --- Deferred pushes


PUSH_SETTINGS = make_settings(vapid_public_key="public", vapid_private_key="private")


async def subscribe(w: World, user_id: uuid.UUID) -> None:
    async with w.app.state.sessionmaker() as session:
        session.add(
            PushSubscription(
                user_id=user_id,
                endpoint=f"https://push.example.test/{uuid.uuid4()}",
                p256dh="p256dh",
                auth="auth",
            )
        )
        await session.commit()


async def push_deferred(
    w: World, notification: Notification, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    """Run the push job for a notification; returns what was sent to the one subscription."""
    sent: list[str] = []
    monkeypatch.setattr(handlers, "get_settings", lambda: PUSH_SETTINGS)
    monkeypatch.setattr(handlers, "webpush", lambda **kwargs: sent.append(str(kwargs["data"])))
    async with w.app.state.sessionmaker() as session:
        await handlers.push_send(session, {"notification_id": str(notification.id)})
        await session.commit()
    return sent


async def test_a_deferred_reminder_is_dropped_when_the_date_has_moved_or_cancelled(
    db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with world(db_app) as w:
        await set_quiet(w, w.partner_id, dt.datetime.now(dt.UTC))
        await subscribe(w, w.partner_id)
        start = when(10, 19)
        fields: dict[str, Any] = {"status": "scheduled", "announced_at": when(-1)}
        unchanged = await seed(w, title="Same", starts_at=start, **fields)
        moved = await seed(w, title="Moved", starts_at=start, **fields)
        cancelled = await seed(w, title="Cancelled", starts_at=start, **fields)
        for itinerary_id in (unchanged, moved, cancelled):
            await run(w, "day", itinerary_id, start)
        rows = await notes(w, w.partner_id, "reminder_day_before")
        by_itinerary = {uuid.UUID(n.data["itinerary_id"]): n for n in rows}
        assert all(n.deliver_after > n.created_at + dt.timedelta(minutes=30) for n in rows)

        async with w.app.state.sessionmaker() as session:
            await session.execute(
                update(Itinerary)
                .where(Itinerary.id == moved)
                .values(starts_at=start + dt.timedelta(days=1))
            )
            await session.execute(
                update(Itinerary).where(Itinerary.id == cancelled).values(status="cancelled")
            )
            await session.commit()

        assert await push_deferred(w, by_itinerary[moved], monkeypatch) == []
        assert await push_deferred(w, by_itinerary[cancelled], monkeypatch) == []
        assert len(await push_deferred(w, by_itinerary[unchanged], monkeypatch)) == 1


async def test_a_deferred_checklist_nudge_is_dropped_when_the_item_changed(
    db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with world(db_app) as w:
        await set_timezone(w, "UTC")
        await set_quiet(w, w.owner_id, dt.datetime.now(dt.UTC))
        await subscribe(w, w.owner_id)
        itinerary_id = await seed(w, status="scheduled", starts_at=when(10, 19))
        due = when(5, 12)
        kept = await add_item(w, itinerary_id, due_at=due.isoformat())
        moved = await add_item(w, itinerary_id, due_at=due.isoformat())
        await run_due(w, kept, due)
        await run_due(w, moved, due)
        rows = {n.data["item_id"]: n for n in await notes(w, w.owner_id, "checklist_due")}
        assert set(rows) == {kept, moved}

        async with w.app.state.sessionmaker() as session:
            await session.execute(
                update(ChecklistItem)
                .where(ChecklistItem.id == uuid.UUID(moved))
                .values(due_at=due + dt.timedelta(days=1))
            )
            await session.commit()

        assert await push_deferred(w, rows[moved], monkeypatch) == []
        assert len(await push_deferred(w, rows[kept], monkeypatch)) == 1


async def test_a_reminder_without_the_recorded_ids_is_pushed_as_before(
    db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with world(db_app) as w:
        async with w.app.state.sessionmaker() as session:
            session.add(
                Notification(
                    user_id=w.partner_id,
                    kind="reminder_day_before",
                    title="Your date is tomorrow",
                    body="Old row.",
                    data={"url": "/itineraries/gone"},
                )
            )
            await session.commit()
        await subscribe(w, w.partner_id)
        (old,) = await notes(w, w.partner_id, "reminder_day_before")
        assert len(await push_deferred(w, old, monkeypatch)) == 1
