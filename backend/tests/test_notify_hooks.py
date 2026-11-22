"""Notification hooks for idea matches and announced itineraries."""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Couple, Notification
from tests.authutil import call
from tests.itinerary_helpers import ITINERARIES, seed, when, world

pytestmark = pytest.mark.db

IDEAS = "/api/v1/ideas"


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


async def shared_idea(client: AsyncClient, title: str = "Picnic") -> str:
    created = await call(client, "POST", IDEAS, {"title": title})
    assert created.status_code == 201, created.text
    idea_id = created.json()["id"]
    shared = await call(client, "POST", f"{IDEAS}/{idea_id}/share")
    assert shared.status_code == 200, shared.text
    return str(idea_id)


async def test_match_notifies_the_earlier_voter_once_after_undo(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        idea_id = await shared_idea(w.owner, "A very good date")
        assert (
            await call(w.owner, "PUT", f"{IDEAS}/{idea_id}/vote", {"value": "yes"})
        ).status_code == 200
        assert (
            await call(w.partner, "PUT", f"{IDEAS}/{idea_id}/vote", {"value": "yes"})
        ).status_code == 200

        first = await notifications(w.app, w.owner_id)
        assert len(first) == 1
        assert first[0].kind == "match"
        assert first[0].title == "It's a match! 💘"
        assert first[0].body == "You both said yes to “A very good date”."
        assert first[0].data == {"url": "/ideas"}
        # The partner's only notification is the "new ideas" one from the owner sharing it.
        assert [n.kind for n in await notifications(w.app, w.partner_id)] == ["ideas_added"]

        undone = await call(w.partner, "POST", "/api/v1/deck/undo")
        assert undone.status_code == 200
        assert (
            await call(w.partner, "PUT", f"{IDEAS}/{idea_id}/vote", {"value": "yes"})
        ).status_code == 200
        assert len(await notifications(w.app, w.owner_id)) == 1


async def test_announced_schedule_notifies_partner_and_reschedules_once(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        async with w.app.state.sessionmaker() as session:
            couple = await session.get(Couple, w.couple_id)
            assert couple is not None
            couple.timezone = "America/New_York"
            await session.commit()
        itinerary_id = await seed(w, title="Dinner out")
        start = when(14, 18)
        response = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            {"starts_at": start.isoformat(), "mode": "announce"},
        )
        assert response.status_code == 200, response.text

        first = await notifications(w.app, w.partner_id)
        assert len(first) == 1
        assert first[0].kind == "date_announced"
        assert first[0].title == "You've got a date 📅"
        assert first[0].data == {"url": f"/itineraries/{itinerary_id}"}
        local = start.astimezone(ZoneInfo("America/New_York"))
        hour = local.strftime("%I").lstrip("0") or "0"
        expected_time = f"{local:%a} {local.day} {local:%b}, {hour}:{local:%M} {local:%p}"
        assert first[0].body == f"Dinner out — {expected_time}"
        assert await notifications(w.app, w.owner_id) == []

        later = start + dt.timedelta(days=1)
        response = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            {"starts_at": later.isoformat(), "mode": "announce"},
        )
        assert response.status_code == 200, response.text
        assert len(await notifications(w.app, w.partner_id)) == 2

        response = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            {"starts_at": later.isoformat(), "mode": "announce"},
        )
        assert response.status_code == 200, response.text
        assert len(await notifications(w.app, w.partner_id)) == 2


async def test_surprise_schedule_does_not_notify_partner(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w)
        response = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            {"starts_at": when(14, 18).isoformat(), "mode": "surprise"},
        )
        assert response.status_code == 200, response.text
        assert await notifications(w.app, w.partner_id) == []
