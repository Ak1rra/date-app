"""Which requests count as signed in: every way a session can be bad must be a 401."""

from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserSession
from app.security.tokens import hash_token
from tests.authutil import browser, patch, post, sign_in

pytestmark = pytest.mark.db

ME = "/api/v1/me"
LOGOUT = "/api/v1/auth/logout"
LOGOUT_ALL = "/api/v1/auth/logout-all"

# Every authenticated endpoint of this wave.
PROTECTED = [
    ("GET", ME, None),
    ("PATCH", ME, {"display_name": "Ann"}),
    ("POST", LOGOUT, None),
    ("POST", LOGOUT_ALL, None),
]


async def call(client: AsyncClient, method: str, path: str, body: Any) -> Any:
    if method == "GET":
        return await client.get(path)
    return await (patch if method == "PATCH" else post)(client, path, json=body)


def assert_unauthenticated(response: Any) -> None:
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
async def test_no_cookie_is_401(db_client: AsyncClient, method: str, path: str, body: Any) -> None:
    assert_unauthenticated(await call(db_client, method, path, body))


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
async def test_garbage_cookie_is_401(
    db_client: AsyncClient, method: str, path: str, body: Any
) -> None:
    db_client.cookies.set("session", "garbage")
    assert_unauthenticated(await call(db_client, method, path, body))


async def test_a_revoked_session_is_401(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    assert (await db_client.get(ME)).status_code == 200
    await db_session.execute(update(UserSession).values(revoked_at=func.now()))
    await db_session.commit()
    assert_unauthenticated(await db_client.get(ME))


async def test_an_expired_session_is_401(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    await db_session.execute(
        update(UserSession).values(expires_at=func.now() - timedelta(seconds=1))
    )
    await db_session.commit()
    assert_unauthenticated(await db_client.get(ME))
    assert (await db_client.get("/api/v1/auth/session")).json()["authenticated"] is False


async def test_a_deleted_user_is_401(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    await db_session.execute(update(User).values(deleted_at=func.now()))
    await db_session.commit()
    assert_unauthenticated(await db_client.get(ME))
    assert (await db_client.get("/api/v1/auth/session")).json() == {
        "authenticated": False,
        "user": None,
    }


async def test_logout_revokes_the_session_and_clears_the_cookie(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    old_cookie = db_client.cookies["session"]
    old_csrf = db_client.cookies["csrf_token"]
    response = await post(db_client, LOGOUT)
    assert response.status_code == 204
    assert response.content == b""
    headers = [h.lower() for h in response.headers.get_list("set-cookie")]
    (cleared,) = [h for h in headers if h.startswith("session=")]
    assert "max-age=0" in cleared and "httponly" in cleared and "path=/" in cleared
    assert "samesite=lax" in cleared
    assert len([h for h in headers if h.startswith("csrf_token=")]) == 1
    assert db_client.cookies["csrf_token"] != old_csrf
    (row,) = (await db_session.execute(select(UserSession))).scalars().all()
    assert row.revoked_at is not None
    # The old cookie is dead even if a client keeps replaying it.
    db_client.cookies.set("session", old_cookie)
    assert_unauthenticated(await db_client.get(ME))
    assert row.token_hash == hash_token(old_cookie)


async def test_logout_only_ends_this_session(db_app: FastAPI, db_client: AsyncClient) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    async with browser(db_app) as phone:
        await sign_in(db_app, phone, "ann@example.com")
        assert (await post(db_client, LOGOUT)).status_code == 204
        assert_unauthenticated(await db_client.get(ME))
        assert (await phone.get(ME)).status_code == 200


async def test_logout_all_kills_every_session_of_the_user_and_only_theirs(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    async with browser(db_app) as phone, browser(db_app) as bob:
        await sign_in(db_app, phone, "ann@example.com")
        await sign_in(db_app, bob, "bob@example.com")
        response = await post(db_client, LOGOUT_ALL)
        assert response.status_code == 204
        cookies = response.headers.get_list("set-cookie")
        assert any(h.lower().startswith("session=") for h in cookies)
        assert_unauthenticated(await db_client.get(ME))
        assert_unauthenticated(await phone.get(ME))
        assert (await bob.get(ME)).json()["email"] == "bob@example.com"
    rows = (await db_session.execute(select(UserSession))).scalars().all()
    assert sum(r.revoked_at is not None for r in rows) == 2
    assert len(rows) == 3


async def test_last_seen_is_touched_at_most_every_five_minutes(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")

    async def last_seen() -> Any:
        row = (await db_session.execute(select(UserSession.last_seen_at))).scalar_one()
        await db_session.rollback()  # end the read so the next one sees fresh data
        return row

    fresh = await last_seen()
    await db_client.get(ME)
    assert await last_seen() == fresh  # inside the 5 minute window: untouched

    old = fresh - timedelta(minutes=6)
    await db_session.execute(update(UserSession).values(last_seen_at=old))
    await db_session.commit()
    await db_client.get(ME)
    assert await last_seen() > old
