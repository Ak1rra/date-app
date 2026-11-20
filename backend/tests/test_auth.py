"""Magic-link sign-in, verify and sign-up (FR-AUTH-1, FR-PAIR-0), against the real database."""

import asyncio
import json
import logging
import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import create_app
from app.models import (
    Couple,
    CoupleConstraints,
    CoupleMember,
    MagicLinkToken,
    PartnerProfile,
    User,
    UserSession,
)
from app.providers.email.base import EmailDeliveryError
from app.providers.email.console import OUTBOX
from app.providers.registry import get_email_sender
from app.security.tokens import hash_token
from tests.authutil import browser, new_link, post, sign_in, token_of
from tests.conftest import ORIGIN, make_settings

pytestmark = pytest.mark.db

MAGIC = "/api/v1/auth/magic-link"
VERIFY = "/api/v1/auth/verify"


async def count(db: AsyncSession, model: Any) -> int:
    return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


async def tokens(db: AsyncSession) -> list[MagicLinkToken]:
    return list((await db.execute(select(MagicLinkToken))).scalars())


# -- POST /auth/magic-link ------------------------------------------------------------------


async def test_magic_link_is_202_for_new_and_existing_emails_alike(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    await sign_in(db_app, db_client, "known@example.com")
    async with browser(db_app) as other:
        known = await post(other, MAGIC, json={"email": "known@example.com"})
        unknown = await post(other, MAGIC, json={"email": "stranger@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json() == {"ok": True}


async def test_magic_link_stores_only_the_hash_and_puts_the_token_in_the_fragment(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await post(db_client, MAGIC, json={"email": "  Ann@Example.COM "})
    assert response.status_code == 202
    (sent,) = OUTBOX
    assert sent["to"] == "ann@example.com"
    link = sent["link"]
    assert link.startswith(f"{ORIGIN}/auth/verify#token=")
    token = token_of(link)
    (row,) = await tokens(db_session)
    assert row.email == "ann@example.com"
    assert row.token_hash == hash_token(token)
    assert token not in json.dumps(
        {c.name: str(getattr(row, c.name)) for c in MagicLinkToken.__table__.columns}
    )
    assert row.used_at is None
    lifetime = row.expires_at - row.created_at
    assert timedelta(minutes=14) < lifetime <= timedelta(minutes=15, seconds=1)


async def test_magic_link_rejects_a_malformed_email(db_client: AsyncClient) -> None:
    response = await post(db_client, MAGIC, json={"email": "not-an-email"})
    assert response.status_code == 422


async def test_sixth_magic_link_per_email_in_15_minutes_is_429(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    for _ in range(5):
        assert (await post(db_client, MAGIC, json={"email": "busy@example.com"})).status_code == 202
    response = await post(db_client, MAGIC, json={"email": "Busy@Example.com"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert 0 < int(response.headers["retry-after"]) <= 15 * 60 + 1
    assert len(await tokens(db_session)) == 5  # the rejected request stored nothing
    # Another address is unaffected, and so is the same address once the window has passed.
    assert (await post(db_client, MAGIC, json={"email": "calm@example.com"})).status_code == 202
    await db_session.execute(
        update(MagicLinkToken).values(created_at=func.now() - timedelta(minutes=16))
    )
    await db_session.commit()
    assert (await post(db_client, MAGIC, json={"email": "busy@example.com"})).status_code == 202


async def test_twenty_first_magic_link_per_ip_in_15_minutes_is_429(
    db_client: AsyncClient,
) -> None:
    for i in range(20):
        response = await post(db_client, MAGIC, json={"email": f"user{i}@example.com"})
        assert response.status_code == 202
    response = await post(db_client, MAGIC, json={"email": "user21@example.com"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert int(response.headers["retry-after"]) > 0


async def test_each_app_has_its_own_ip_limiter(db_schema: str) -> None:
    first, second = (
        create_app(make_settings(database_url=db_schema)),
        create_app(make_settings(database_url=db_schema)),
    )
    async with browser(first) as a:
        for i in range(21):
            await post(a, MAGIC, json={"email": f"x{i}@example.com"})
    async with browser(second) as b:
        assert (await post(b, MAGIC, json={"email": "fresh@example.com"})).status_code == 202


class FailingSender:
    async def send(self, **_: Any) -> None:
        raise EmailDeliveryError("boom for ann@example.com")


async def test_email_delivery_failure_still_returns_202_and_logs_no_address(
    db_app: FastAPI,
    db_client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_app.dependency_overrides[get_email_sender] = lambda: FailingSender()
    caplog.set_level(logging.DEBUG)
    response = await post(db_client, MAGIC, json={"email": "ann@example.com"})
    assert response.status_code == 202
    assert response.json() == {"ok": True}
    assert len(await tokens(db_session)) == 1  # the row stays
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "ann@example.com" not in json.dumps(errors[0].__dict__, default=str)
    assert errors[0].exc_info is None


# -- POST /auth/verify ----------------------------------------------------------------------


def set_cookies(response: Any) -> dict[str, str]:
    """Set-Cookie header per cookie name, lower-cased."""
    out: dict[str, str] = {}
    for header in response.headers.get_list("set-cookie"):
        out[header.split("=", 1)[0]] = header.lower()
    return out


async def test_verify_sets_a_hardened_session_cookie_and_rotates_csrf(
    db_schema: str,
) -> None:
    app = create_app(make_settings(database_url=db_schema, cookie_secure=True))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        await client.get("/api/v1/meta")
        before = client.cookies["csrf_token"]
        response = await post(
            client, VERIFY, json={"token": token_of(await new_link(app, "ann@example.com"))}
        )
        assert response.status_code == 200
        cookies = set_cookies(response)
        session_cookie = cookies["session"]
        for attr in ("httponly", "samesite=lax", "path=/", "secure", "max-age=2592000"):
            assert attr in session_cookie, session_cookie
        csrf_cookie = cookies["csrf_token"]
        assert "httponly" not in csrf_cookie
        assert "samesite=lax" in csrf_cookie and "secure" in csrf_cookie
        assert len(response.headers.get_list("set-cookie")) == 2  # one csrf cookie, not two
        assert client.cookies["csrf_token"] != before


async def test_verify_success_body_and_session_row(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    link = await new_link(db_app, "Ann@Example.com")
    response = await post(
        db_client,
        VERIFY,
        json={"token": token_of(link)},
        headers={"User-Agent": "x" * 500},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    user = body["user"]
    assert user["email"] == "ann@example.com"
    assert user["display_name"] is None
    assert user["timezone"] == "UTC"
    assert user["role"] == "owner"
    assert uuid.UUID(user["couple_id"])
    cookie = db_client.cookies["session"]
    (row,) = (await db_session.execute(select(UserSession))).scalars().all()
    assert row.token_hash == hash_token(cookie)
    assert cookie != row.token_hash
    assert str(row.user_id) == user["id"]
    assert row.user_agent == "x" * 200
    assert row.revoked_at is None
    assert timedelta(days=29) < row.expires_at - row.created_at <= timedelta(days=30, seconds=1)
    (token_row,) = await tokens(db_session)
    assert token_row.used_at is not None


async def _expire_tokens(db: AsyncSession) -> None:
    await db.execute(update(MagicLinkToken).values(expires_at=func.now() - timedelta(minutes=1)))
    await db.commit()


async def test_every_bad_token_gets_the_same_400(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    bodies: list[tuple[str, Any]] = []

    unknown = await post(db_client, VERIFY, json={"token": "not-a-real-token"})
    bodies.append(("unknown", unknown))

    token = token_of(await new_link(db_app, "reuse@example.com"))
    assert (await post(db_client, VERIFY, json={"token": token})).status_code == 200
    async with browser(db_app) as other:
        bodies.append(("reused", await post(other, VERIFY, json={"token": token})))

    token = token_of(await new_link(db_app, "late@example.com"))
    await _expire_tokens(db_session)
    async with browser(db_app) as other:
        bodies.append(("expired", await post(other, VERIFY, json={"token": token})))

    await db_session.execute(
        update(User).where(User.email == "reuse@example.com").values(deleted_at=func.now())
    )
    await db_session.commit()
    token = token_of(await new_link(db_app, "reuse@example.com"))
    async with browser(db_app) as other:
        bodies.append(("deleted user", await post(other, VERIFY, json={"token": token})))

    for name, response in bodies:
        assert response.status_code == 400, name
        assert response.json() == unknown.json(), name
        assert "session" not in set_cookies(response), name
    assert unknown.json()["error"]["code"] == "invalid_token"
    # The deleted user got no new account or session.
    assert await count(db_session, UserSession) == 1


async def test_concurrent_verifies_of_one_token_have_exactly_one_winner(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    token = token_of(await new_link(db_app, "race@example.com"))
    async with browser(db_app) as a, browser(db_app) as b, browser(db_app) as c:
        results = await asyncio.gather(
            *(post(client, VERIFY, json={"token": token}) for client in (a, b, c))
        )
    assert sorted(r.status_code for r in results) == [200, 400, 400]
    assert await count(db_session, UserSession) == 1
    assert await count(db_session, User) == 1


async def test_two_links_for_one_new_email_redeemed_at_once_make_one_account(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    t1 = token_of(await new_link(db_app, "twin@example.com"))
    t2 = token_of(await new_link(db_app, "twin@example.com"))
    async with browser(db_app) as a, browser(db_app) as b:
        results = await asyncio.gather(
            post(a, VERIFY, json={"token": t1}), post(b, VERIFY, json={"token": t2})
        )
    assert [r.status_code for r in results] == [200, 200]
    assert await count(db_session, User) == 1
    assert await count(db_session, Couple) == 1
    assert await count(db_session, UserSession) == 2


async def test_first_verify_creates_the_couple_space_and_a_second_sign_in_does_not(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await sign_in(db_app, db_client, "ann@example.com", timezone="America/New_York")

    (user,) = (await db_session.execute(select(User))).scalars().all()
    (couple,) = (await db_session.execute(select(Couple))).scalars().all()
    (member,) = (await db_session.execute(select(CoupleMember))).scalars().all()
    profiles = {
        p.slot: p for p in (await db_session.execute(select(PartnerProfile))).scalars().all()
    }
    (constraints,) = (await db_session.execute(select(CoupleConstraints))).scalars().all()

    assert user.email == "ann@example.com"
    assert user.timezone == couple.timezone == "America/New_York"
    assert couple.status == "active"
    assert (member.couple_id, member.user_id, member.role) == (couple.id, user.id, "owner")
    assert member.left_at is None
    assert set(profiles) == {"owner", "partner"}
    assert profiles["owner"].couple_id == profiles["partner"].couple_id == couple.id
    assert profiles["owner"].subject_user_id == user.id
    assert profiles["partner"].subject_user_id is None
    assert constraints.couple_id == couple.id
    assert constraints.budget_min is None and constraints.transport_modes == []

    async with browser(db_app) as again:
        response = await sign_in(db_app, again, "ANN@example.com", timezone="Asia/Tokyo")
    assert response.json()["user"]["id"] == str(user.id)
    assert response.json()["user"]["couple_id"] == str(couple.id)
    for model, expected in (
        (User, 1),
        (Couple, 1),
        (CoupleMember, 1),
        (PartnerProfile, 2),
        (CoupleConstraints, 1),
        (UserSession, 2),
    ):
        assert await count(db_session, model) == expected, model.__name__
    await db_session.refresh(user)
    assert user.timezone == "America/New_York"  # a later sign-in doesn't rewrite it


@pytest.mark.parametrize("timezone", [None, "", "Not/AZone", "../etc/passwd", "x" * 500, "utc"])
async def test_unusable_timezone_is_ignored_not_rejected(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession, timezone: str | None
) -> None:
    response = await sign_in(db_app, db_client, "ann@example.com", timezone=timezone)
    assert response.json()["user"]["timezone"] == "UTC"
    (couple,) = (await db_session.execute(select(Couple))).scalars().all()
    assert couple.timezone == "UTC"


async def test_verify_ip_limit_is_30_per_15_minutes(db_client: AsyncClient) -> None:
    for _ in range(30):
        assert (await post(db_client, VERIFY, json={"token": "nope"})).status_code == 400
    response = await post(db_client, VERIFY, json={"token": "nope"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert int(response.headers["retry-after"]) > 0


# -- CSRF -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", MAGIC, {"email": "ann@example.com"}),
        ("POST", VERIFY, {"token": "x"}),
        ("POST", "/api/v1/auth/logout", None),
        ("POST", "/api/v1/auth/logout-all", None),
        ("PATCH", "/api/v1/me", {"display_name": "Ann"}),
    ],
)
async def test_unsafe_auth_requests_without_a_csrf_header_are_403(
    db_client: AsyncClient, method: str, path: str, body: Any
) -> None:
    await db_client.get("/api/v1/meta")  # holds the cookie, sends no header
    response = await db_client.request(method, path, json=body)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"
    assert not OUTBOX


# -- GET /auth/session ----------------------------------------------------------------------


async def test_session_reports_signed_out_without_a_401(db_client: AsyncClient) -> None:
    response = await db_client.get("/api/v1/auth/session")
    assert response.status_code == 200
    assert response.json() == {"authenticated": False, "user": None}
    assert response.headers["cache-control"] == "no-store"


async def test_session_with_a_garbage_cookie_is_signed_out(db_client: AsyncClient) -> None:
    db_client.cookies.set("session", "garbage")
    response = await db_client.get("/api/v1/auth/session")
    assert response.status_code == 200
    assert response.json() == {"authenticated": False, "user": None}


async def test_session_reports_the_signed_in_user(db_app: FastAPI, db_client: AsyncClient) -> None:
    signed_in = (await sign_in(db_app, db_client, "ann@example.com")).json()["user"]
    response = await db_client.get("/api/v1/auth/session")
    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "user": signed_in}
    assert set(signed_in) == {"id", "email", "display_name", "timezone", "couple_id", "role"}


# -- Logging --------------------------------------------------------------------------------


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, **kwargs: Any) -> None:
        self.sent.append(kwargs)


async def test_no_token_link_or_address_is_logged(
    db_app: FastAPI,
    db_client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The console sender logs the link on purpose (dev outbox); this checks the app's own code.
    sender = RecordingSender()
    db_app.dependency_overrides[get_email_sender] = lambda: sender
    caplog.set_level(logging.DEBUG)
    email = "secret.person@example.com"
    assert (await post(db_client, MAGIC, json={"email": email})).status_code == 202
    link = sender.sent[0]["link"]
    token = token_of(link)
    assert (await post(db_client, VERIFY, json={"token": token})).status_code == 200
    assert (await post(db_client, VERIFY, json={"token": token})).status_code == 400
    session_token = db_client.cookies["session"]
    await db_client.get("/api/v1/me")
    await post(db_client, "/api/v1/auth/logout")

    assert caplog.records  # the access log, at least
    dump = caplog.text + json.dumps([r.__dict__ for r in caplog.records], default=str)
    for secret in (token, link, email, "secret.person", session_token):
        assert secret not in dump


async def test_concurrent_magic_links_cannot_pass_the_per_email_limit(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    for _ in range(4):
        await new_link(db_app, "busy@example.com")
    async with browser(db_app) as a, browser(db_app) as b, browser(db_app) as c:
        results = await asyncio.gather(
            *(post(client, MAGIC, json={"email": "busy@example.com"}) for client in (a, b, c))
        )
    assert sorted(r.status_code for r in results) == [202, 429, 429]
    assert len(await tokens(db_session)) == 5
