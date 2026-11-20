"""GET/PATCH /me: a user only ever sees and changes themselves."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from tests.authutil import browser, patch, sign_in

pytestmark = pytest.mark.db

ME = "/api/v1/me"


async def test_get_me_returns_the_signed_in_user(db_app: FastAPI, db_client: AsyncClient) -> None:
    signed_in = (await sign_in(db_app, db_client, "ann@example.com")).json()["user"]
    response = await db_client.get(ME)
    assert response.status_code == 200
    assert response.json() == signed_in
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["role"] == "owner"


async def test_patch_me_updates_display_name_and_timezone(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    response = await patch(
        db_client, ME, json={"display_name": "  Ann  ", "timezone": "Europe/Paris"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "Ann"
    assert body["timezone"] == "Europe/Paris"
    assert (await db_client.get(ME)).json() == body
    (user,) = (await db_session.execute(select(User))).scalars().all()
    assert (user.display_name, user.timezone) == ("Ann", "Europe/Paris")

    # Fields left out stay as they are.
    only_name = await patch(db_client, ME, json={"display_name": "Annie"})
    assert only_name.json()["timezone"] == "Europe/Paris"
    assert (await patch(db_client, ME, json={})).json()["display_name"] == "Annie"


@pytest.mark.parametrize("timezone", ["Mars/Olympus", "", "utc", "../etc/passwd", 5])
async def test_patch_me_with_a_bad_timezone_is_422(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession, timezone: object
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    response = await patch(db_client, ME, json={"timezone": timezone})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    (user,) = (await db_session.execute(select(User))).scalars().all()
    assert user.timezone == "UTC"


@pytest.mark.parametrize("name", ["", "   ", "x" * 121])
async def test_patch_me_with_a_bad_display_name_is_422(
    db_app: FastAPI, db_client: AsyncClient, name: str
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    response = await patch(db_client, ME, json={"display_name": name})
    assert response.status_code == 422
    assert (await patch(db_client, ME, json={"display_name": "x" * 120})).status_code == 200


async def test_users_in_different_couples_only_ever_see_themselves(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with browser(db_app) as ann, browser(db_app) as bob:
        ann_user = (await sign_in(db_app, ann, "ann@example.com")).json()["user"]
        bob_user = (await sign_in(db_app, bob, "bob@example.com")).json()["user"]
        assert ann_user["id"] != bob_user["id"]
        assert ann_user["couple_id"] != bob_user["couple_id"]

        await patch(ann, ME, json={"display_name": "Ann"})
        await patch(bob, ME, json={"display_name": "Bob", "timezone": "Asia/Tokyo"})

        assert (await ann.get(ME)).json() == {**ann_user, "display_name": "Ann"}
        assert (await bob.get(ME)).json() == {
            **bob_user,
            "display_name": "Bob",
            "timezone": "Asia/Tokyo",
        }
        # Nothing in the request can name another user: an id in the path or body is not a route.
        assert (await ann.get(f"{ME}/{bob_user['id']}")).status_code == 404
        smuggled = await patch(ann, ME, json={"id": bob_user["id"], "email": "bob@example.com"})
        assert smuggled.json()["id"] == ann_user["id"]
        assert smuggled.json()["email"] == "ann@example.com"
        session = (await ann.get("/api/v1/auth/session")).json()
        assert session["user"]["id"] == ann_user["id"]

    users = {u.email: u for u in (await db_session.execute(select(User))).scalars().all()}
    assert users["ann@example.com"].display_name == "Ann"
    assert users["bob@example.com"].display_name == "Bob"
    assert users["bob@example.com"].timezone == "Asia/Tokyo"
    assert users["ann@example.com"].timezone == "UTC"
