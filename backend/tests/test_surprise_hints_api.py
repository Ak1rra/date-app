"""Surprise mode, planner side: hints, the reveal time, reveal now, and the due-time worker job."""

import datetime as dt
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.models import CoupleMember, Itinerary, Job, Notification, SurpriseHint
from app.services.surprise import process_due
from app.worker import handlers
from tests.authutil import call
from tests.itinerary_helpers import ITINERARIES, World, seed, when, world

pytestmark = pytest.mark.db


def hints_url(itinerary_id: uuid.UUID, hint_id: str | None = None) -> str:
    base = f"{ITINERARIES}/{itinerary_id}/hints"
    return base if hint_id is None else f"{base}/{hint_id}"


async def seed_surprise(w: World, **fields: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "title": "Secret rooftop dinner",
        "is_surprise": True,
        "status": "scheduled",
        "starts_at": when(5, 18),
        "ends_at": when(5, 21),
        "reveal_at": when(5, 18),
    }
    values.update(fields)
    return await seed(w, **values)


async def notifications(app: FastAPI, user_id: uuid.UUID) -> list[Notification]:
    async with app.state.sessionmaker() as session:
        return list(
            (
                await session.execute(
                    select(Notification)
                    .where(Notification.user_id == user_id)
                    .order_by(Notification.created_at, Notification.id)
                )
            )
            .scalars()
            .all()
        )


async def jobs(app: FastAPI) -> list[Job]:
    async with app.state.sessionmaker() as session:
        return list(
            (await session.execute(select(Job).where(Job.kind == "surprise.due"))).scalars().all()
        )


async def run_due(app: FastAPI, itinerary_id: uuid.UUID) -> None:
    async with app.state.sessionmaker() as session:
        await handlers.surprise_due(session, {"itinerary_id": str(itinerary_id)})
        await session.commit()


async def backdate(app: FastAPI, itinerary_id: uuid.UUID, *, hints: bool, reveal: bool) -> None:
    """Move the hints' release times and/or the reveal time into the past."""
    past = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    async with app.state.sessionmaker() as session:
        if hints:
            for hint in (
                (
                    await session.execute(
                        select(SurpriseHint).where(SurpriseHint.itinerary_id == itinerary_id)
                    )
                )
                .scalars()
                .all()
            ):
                hint.release_at = past
        if reveal:
            itinerary = await session.get(Itinerary, itinerary_id)
            assert itinerary is not None
            itinerary.reveal_at = past
        await session.commit()


async def test_planner_hint_crud(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w)
        later = when(4, 9)
        created = await call(
            w.owner,
            "POST",
            hints_url(itinerary_id),
            {
                "kind": "dress_code",
                "text": "  Wear something cozy  ",
                "release_at": later.isoformat(),
            },
        )
        assert created.status_code == 201, created.text
        hint = created.json()
        assert hint["text"] == "Wear something cozy"
        assert hint["released_at"] is None
        assert dt.datetime.fromisoformat(hint["release_at"]) == later

        earlier = await call(
            w.owner,
            "POST",
            hints_url(itinerary_id),
            {"kind": "bring", "text": "A jacket", "release_at": when(3, 9).isoformat()},
        )
        assert earlier.status_code == 201, earlier.text

        listed = await call(w.owner, "GET", hints_url(itinerary_id))
        assert [h["text"] for h in listed.json()] == ["A jacket", "Wear something cozy"]

        patched = await call(
            w.owner, "PATCH", hints_url(itinerary_id, hint["id"]), {"text": "Dress warmly"}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["text"] == "Dress warmly"
        assert patched.json()["kind"] == "dress_code"
        assert dt.datetime.fromisoformat(patched.json()["release_at"]) == later

        deleted = await call(w.owner, "DELETE", hints_url(itinerary_id, hint["id"]))
        assert deleted.status_code == 204
        gone = await call(w.owner, "DELETE", hints_url(itinerary_id, hint["id"]))
        assert gone.status_code == 404
        assert gone.json()["error"]["code"] == "not_found"


async def test_hint_validation(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w)
        for body in (
            {"kind": "bring", "text": "   "},
            {"kind": "bring", "text": "x" * 301},
            {"kind": "nope", "text": "ok"},
        ):
            response = await call(w.owner, "POST", hints_url(itinerary_id), body)
            assert response.status_code == 422, body


async def test_hint_of_another_itinerary_is_not_found(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        first = await seed_surprise(w)
        second = await seed_surprise(w)
        created = await call(
            w.owner,
            "POST",
            hints_url(first),
            {"kind": "eat", "text": "Come hungry", "release_at": when(3, 9).isoformat()},
        )
        hint_id = created.json()["id"]
        for method, body in (("PATCH", {"text": "x"}), ("DELETE", None)):
            response = await call(w.owner, method, hints_url(second, hint_id), body)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "not_found"


async def test_at_most_twenty_hints(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w)
        body = {"kind": "custom", "text": "Clue", "release_at": when(3, 9).isoformat()}
        for _ in range(20):
            assert (await call(w.owner, "POST", hints_url(itinerary_id), body)).status_code == 201
        response = await call(w.owner, "POST", hints_url(itinerary_id), body)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "too_many_hints"


async def test_access_and_state_guards(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        body = {"kind": "custom", "text": "Clue"}
        hidden = await seed_surprise(w)
        assert (await call(w.partner, "GET", hints_url(hidden))).status_code == 404
        assert (await call(w.partner, "POST", hints_url(hidden), body)).status_code == 404

        not_surprise = await seed(
            w, status="scheduled", starts_at=when(5), announced_at=when(-1), is_surprise=False
        )
        response = await call(w.owner, "POST", hints_url(not_surprise), body)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_a_surprise"
        assert (await call(w.partner, "POST", hints_url(not_surprise), body)).status_code == 403

        cancelled = await seed_surprise(w, status="cancelled")
        response = await call(w.owner, "POST", hints_url(cancelled), body)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_editable"


async def test_hint_without_release_time_is_released_at_once(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w)
        created = await call(
            w.owner, "POST", hints_url(itinerary_id), {"kind": "eat", "text": "Come hungry"}
        )
        assert created.status_code == 201, created.text
        assert created.json()["released_at"] is not None

        sent = await notifications(w.app, w.partner_id)
        assert len(sent) == 1
        assert sent[0].kind == "hint_released"
        assert sent[0].body == "Come hungry"
        assert sent[0].data == {"url": f"/surprise/{itinerary_id}"}
        assert "Secret rooftop dinner" not in f"{sent[0].title} {sent[0].body}"
        assert await notifications(w.app, w.owner_id) == []

        again = await call(
            w.owner, "PATCH", hints_url(itinerary_id, created.json()["id"]), {"text": "Never mind"}
        )
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "already_released"


async def test_patch_changes_only_fields_present(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w)
        later = when(4, 9)
        created = await call(
            w.owner,
            "POST",
            hints_url(itinerary_id),
            {"kind": "weather", "text": "Bring a layer", "release_at": later.isoformat()},
        )
        hint_id = created.json()["id"]
        patched = await call(w.owner, "PATCH", hints_url(itinerary_id, hint_id), {"kind": "bring"})
        assert patched.status_code == 200, patched.text
        assert patched.json()["kind"] == "bring"
        assert patched.json()["text"] == "Bring a layer"
        assert dt.datetime.fromisoformat(patched.json()["release_at"]) == later

        released = await call(
            w.owner, "PATCH", hints_url(itinerary_id, hint_id), {"release_at": None}
        )
        assert released.json()["released_at"] is not None
        assert len(await notifications(w.app, w.partner_id)) == 1


async def test_draft_surprise_hints_wait_for_schedule(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(
            w, status="draft", starts_at=None, ends_at=None, reveal_at=None
        )
        created = await call(
            w.owner, "POST", hints_url(itinerary_id), {"kind": "ready_by", "text": "Be ready at 6"}
        )
        assert created.status_code == 201, created.text
        assert created.json()["released_at"] is None
        assert await notifications(w.app, w.partner_id) == []
        assert await jobs(w.app) == []

        scheduled = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            {"starts_at": when(5, 18).isoformat(), "mode": "surprise"},
        )
        assert scheduled.status_code == 200, scheduled.text
        listed = await call(w.owner, "GET", hints_url(itinerary_id))
        assert listed.json()[0]["released_at"] is not None
        sent = await notifications(w.app, w.partner_id)
        assert [n.body for n in sent] == ["Be ready at 6"]
        assert len(await jobs(w.app)) == 1  # the reveal at the start time


async def test_future_times_enqueue_jobs_and_the_handler_releases(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w)
        release = when(3, 9)
        created = await call(
            w.owner,
            "POST",
            hints_url(itinerary_id),
            {"kind": "energy", "text": "Wear comfy shoes", "release_at": release.isoformat()},
        )
        assert created.status_code == 201, created.text
        # A second hint at the same time must not enqueue a second job.
        await call(
            w.owner,
            "POST",
            hints_url(itinerary_id),
            {"kind": "bring", "text": "Camera", "release_at": release.isoformat()},
        )
        queued = await jobs(w.app)
        keys = {job.dedupe_key for job in queued}
        assert keys == {
            f"surprise:{itinerary_id}:{release.isoformat()}",
            f"surprise:{itinerary_id}:{when(5, 18).isoformat()}",
        }
        assert all(job.payload == {"itinerary_id": str(itinerary_id)} for job in queued)
        assert await notifications(w.app, w.partner_id) == []

        await backdate(w.app, itinerary_id, hints=True, reveal=False)
        await run_due(w.app, itinerary_id)
        await run_due(w.app, itinerary_id)
        sent = await notifications(w.app, w.partner_id)
        assert sorted(n.body for n in sent) == ["Camera", "Wear comfy shoes"]
        assert all(n.kind == "hint_released" for n in sent)


async def test_reveal_at_and_the_handler_reveals_once(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w)
        moved = when(5, 19)
        response = await call(
            w.owner,
            "PUT",
            f"{ITINERARIES}/{itinerary_id}/reveal-at",
            {"reveal_at": moved.isoformat()},
        )
        assert response.status_code == 200, response.text
        assert dt.datetime.fromisoformat(response.json()["reveal_at"]) == moved
        assert response.json()["revealed_at"] is None
        assert {job.dedupe_key for job in await jobs(w.app)} == {
            f"surprise:{itinerary_id}:{moved.isoformat()}"
        }

        await backdate(w.app, itinerary_id, hints=False, reveal=True)
        await run_due(w.app, itinerary_id)
        await run_due(w.app, itinerary_id)
        sent = await notifications(w.app, w.partner_id)
        assert len(sent) == 1
        assert sent[0].kind == "surprise_revealed"
        assert sent[0].title == "Your surprise is revealed ✨"
        assert sent[0].data == {"url": f"/itineraries/{itinerary_id}"}
        assert "Secret rooftop dinner" not in f"{sent[0].title} {sent[0].body}"
        async with w.app.state.sessionmaker() as session:
            itinerary = await session.get(Itinerary, itinerary_id)
            assert itinerary is not None and itinerary.revealed_at is not None


async def test_reveal_at_rules(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, reveal_at=when(5, 19))
        url = f"{ITINERARIES}/{itinerary_id}/reveal-at"

        late = await call(w.owner, "PUT", url, {"reveal_at": when(5, 22).isoformat()})
        assert late.status_code == 422
        assert late.json()["error"]["code"] == "invalid_reveal_at"

        reset = await call(w.owner, "PUT", url, {"reveal_at": None})
        assert reset.status_code == 200, reset.text
        assert dt.datetime.fromisoformat(reset.json()["reveal_at"]) == when(5, 18)

        done = await seed_surprise(w, revealed_at=when(-1, 9))
        response = await call(
            w.owner,
            "PUT",
            f"{ITINERARIES}/{done}/reveal-at",
            {"reveal_at": when(5, 19).isoformat()},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "already_revealed"


async def test_past_reveal_at_reveals_in_the_request(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w)
        response = await call(
            w.owner,
            "PUT",
            f"{ITINERARIES}/{itinerary_id}/reveal-at",
            {"reveal_at": (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)).isoformat()},
        )
        # Before the date has started, a reveal in the past is not after the end: allowed.
        assert response.status_code == 200, response.text
        assert response.json()["revealed_at"] is not None
        assert len(await notifications(w.app, w.partner_id)) == 1


async def test_reveal_now(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w)
        await call(
            w.owner,
            "POST",
            hints_url(itinerary_id),
            {"kind": "bring", "text": "Camera", "release_at": when(3, 9).isoformat()},
        )
        response = await call(w.owner, "POST", f"{ITINERARIES}/{itinerary_id}/reveal")
        assert response.status_code == 200, response.text
        assert response.json()["revealed_at"] is not None

        sent = await notifications(w.app, w.partner_id)
        assert [n.kind for n in sent] == ["surprise_revealed"]
        assert sent[0].data == {"url": f"/itineraries/{itinerary_id}"}
        text = f"{sent[0].title} {sent[0].body}"
        assert "Secret rooftop dinner" not in text

        again = await call(w.owner, "POST", f"{ITINERARIES}/{itinerary_id}/reveal")
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "already_revealed"
        assert len(await notifications(w.app, w.partner_id)) == 1


async def test_reveal_needs_a_scheduled_date(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, status="draft")
        response = await call(w.owner, "POST", f"{ITINERARIES}/{itinerary_id}/reveal")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "invalid_status"


async def test_unpaired_couple_still_releases_hints(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w)
        async with w.app.state.sessionmaker() as session:
            member = (
                await session.execute(
                    select(CoupleMember).where(CoupleMember.user_id == w.partner_id)
                )
            ).scalar_one()
            member.left_at = dt.datetime.now(dt.UTC)
            await session.commit()

        created = await call(
            w.owner, "POST", hints_url(itinerary_id), {"kind": "eat", "text": "Come hungry"}
        )
        assert created.status_code == 201, created.text
        assert created.json()["released_at"] is not None
        assert await notifications(w.app, w.partner_id) == []


async def test_reveal_at_is_checked_against_ends_at_without_a_start(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, starts_at=None, reveal_at=None, ends_at=when(5, 21))
        url = f"{ITINERARIES}/{itinerary_id}/reveal-at"
        late = await call(w.owner, "PUT", url, {"reveal_at": when(5, 22).isoformat()})
        assert late.status_code == 422
        assert late.json()["error"]["code"] == "invalid_reveal_at"
        fine = await call(w.owner, "PUT", url, {"reveal_at": when(5, 20).isoformat()})
        assert fine.status_code == 200, fine.text


async def test_process_due_rereads_state_changed_behind_a_loaded_itinerary(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        past = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
        itinerary_id = await seed_surprise(w, reveal_at=past)
        async with w.app.state.sessionmaker() as stale, w.app.state.sessionmaker() as other:
            itinerary = await stale.get(Itinerary, itinerary_id)
            assert itinerary is not None and itinerary.reveal_at == past
            # The planner moves the reveal later after the worker loaded the itinerary.
            moved = await other.get(Itinerary, itinerary_id)
            assert moved is not None
            moved.reveal_at = when(5, 19)
            await other.commit()

            await process_due(stale, itinerary, w.app.state.settings, dt.datetime.now(dt.UTC))
            await stale.commit()
            assert itinerary.revealed_at is None
        assert await notifications(w.app, w.partner_id) == []
