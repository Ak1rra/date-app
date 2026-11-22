"""Notification centre and Web Push API tests."""

import datetime as dt

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Notification, User
from tests.authutil import call, joined_couple, sign_in

pytestmark = pytest.mark.db


async def test_settings_round_trip_and_push_config(db_app: FastAPI, db_client: AsyncClient) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    response = await db_client.get("/api/v1/me/notification-settings")
    assert response.status_code == 200
    assert len(response.json()["kinds"]) == 13
    changed = await call(
        db_client,
        "PUT",
        "/api/v1/me/notification-settings",
        {"kinds": {"match": False}, "quiet_start": "23:00", "quiet_end": "07:00"},
    )
    assert changed.status_code == 200
    assert changed.json()["kinds"]["match"] is False
    assert changed.json()["quiet_start"] == "23:00"
    assert (await db_client.get("/api/v1/notifications/push-config")).json() == {"public_key": None}


async def test_push_subscription_reassignment_and_delete(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    async with joined_couple(db_app) as (owner, partner):
        payload = {
            "endpoint": "https://push.example.test/shared",
            "keys": {"p256dh": "p", "auth": "a"},
        }
        first = await call(owner, "POST", "/api/v1/me/push-subscriptions", payload)
        assert first.status_code == 201
        moved = await call(partner, "POST", "/api/v1/me/push-subscriptions", payload)
        assert moved.status_code == 201
        assert (
            await call(
                owner,
                "DELETE",
                "/api/v1/me/push-subscriptions?endpoint=https%3A%2F%2Fpush.example.test%2Fshared",
            )
        ).status_code == 204
        assert (
            await call(
                partner,
                "DELETE",
                "/api/v1/me/push-subscriptions?endpoint=https%3A%2F%2Fpush.example.test%2Fshared",
            )
        ).status_code == 204


async def test_notification_id_is_private(db_app: FastAPI, db_client: AsyncClient) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    # A syntactically valid UUID that does not belong to this user is indistinguishable from absent.
    response = await call(
        db_client,
        "POST",
        "/api/v1/notifications/00000000-0000-0000-0000-000000000001/read",
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_future_notification_cannot_be_marked_read(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    async with db_app.state.sessionmaker() as session:
        user = (
            await session.execute(select(User).where(User.email == "ann@example.com"))
        ).scalar_one()
        notification = Notification(
            user_id=user.id,
            kind="match",
            title="Future",
            body="Not yet",
            deliver_after=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        )
        session.add(notification)
        await session.commit()
        notification_id = notification.id
    response = await call(db_client, "POST", f"/api/v1/notifications/{notification_id}/read")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_read_all_only_marks_delivered_notifications(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    async with db_app.state.sessionmaker() as session:
        user = (
            await session.execute(select(User).where(User.email == "ann@example.com"))
        ).scalar_one()
        future = Notification(
            user_id=user.id,
            kind="match",
            title="Future",
            body="Not yet",
            deliver_after=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        )
        delivered = Notification(
            user_id=user.id,
            kind="match",
            title="Delivered",
            body="Now",
            deliver_after=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1),
        )
        session.add_all([future, delivered])
        await session.commit()
        future_id, delivered_id = future.id, delivered.id
    response = await call(db_client, "POST", "/api/v1/notifications/read-all")
    assert response.status_code == 200
    assert response.json() == {"updated": 1}
    async with db_app.state.sessionmaker() as session:
        future = await session.get(Notification, future_id)
        delivered = await session.get(Notification, delivered_id)
    assert future is not None and future.read_at is None
    assert delivered is not None and delivered.read_at is not None


async def test_mark_read_is_idempotent_for_delivered_notification(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    async with db_app.state.sessionmaker() as session:
        user = (
            await session.execute(select(User).where(User.email == "ann@example.com"))
        ).scalar_one()
        notification = Notification(
            user_id=user.id,
            kind="match",
            title="Delivered",
            body="Now",
            deliver_after=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1),
        )
        session.add(notification)
        await session.commit()
        notification_id = notification.id
    first = await call(db_client, "POST", f"/api/v1/notifications/{notification_id}/read")
    second = await call(db_client, "POST", f"/api/v1/notifications/{notification_id}/read")
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"] == str(notification_id)
    assert first.json()["read_at"] == second.json()["read_at"]


async def test_list_and_count_show_only_my_delivered_notifications(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (owner, partner):
        owner_id = (await owner.get("/api/v1/me")).json()["id"]
        partner_id = (await partner.get("/api/v1/me")).json()["id"]
        now = dt.datetime.now(dt.UTC)
        async with db_app.state.sessionmaker() as session:
            session.add_all(
                [
                    Notification(
                        user_id=owner_id,
                        kind="match",
                        title="Mine",
                        body="b",
                        deliver_after=now - dt.timedelta(minutes=1),
                    ),
                    Notification(
                        user_id=owner_id,
                        kind="match",
                        title="Later",
                        body="b",
                        deliver_after=now + dt.timedelta(hours=1),
                    ),
                    Notification(
                        user_id=partner_id,
                        kind="match",
                        title="Theirs",
                        body="b",
                        deliver_after=now - dt.timedelta(minutes=1),
                    ),
                ]
            )
            await session.commit()
        listed = (await owner.get("/api/v1/notifications")).json()
        assert [n["title"] for n in listed] == ["Mine"]
        assert (await owner.get("/api/v1/notifications/unread-count")).json() == {"count": 1}
        theirs = (await partner.get("/api/v1/notifications")).json()
        assert [n["title"] for n in theirs] == ["Theirs"]
