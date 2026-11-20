"""NO_LOGIN mode: the husband/wife picker (no PIN, trust), against the real database."""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import delete, func, select, update
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
from app.security.tokens import hash_token
from app.services.auth import HUSBAND_EMAIL, WIFE_EMAIL
from tests.authutil import browser, new_link, post, token_of
from tests.conftest import make_settings
from tests.test_auth import count

pytestmark = pytest.mark.db

CHOOSE = "/api/v1/auth/choose-profile"


@pytest.fixture
def nl_app(db_schema: str) -> FastAPI:
    return create_app(make_settings(database_url=db_schema, no_login=True))


@pytest.fixture
async def nl_client(nl_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with browser(nl_app) as client:
        yield client


async def choose(client: AsyncClient, profile: str) -> Any:
    return await post(client, CHOOSE, json={"profile": profile})


async def household_counts(db: AsyncSession) -> dict[str, int]:
    await db.rollback()  # end any earlier read so this one sees fresh data
    return {
        model.__name__: await count(db, model)
        for model in (User, Couple, CoupleMember, PartnerProfile, CoupleConstraints)
    }


# -- switched off / on ------------------------------------------------------------------------


async def test_choose_profile_is_404_when_no_login_is_off(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await choose(db_client, "husband")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert await count(db_session, User) == 0
    assert "session" not in db_client.cookies


async def test_magic_link_and_verify_are_404_when_no_login_is_on(
    nl_app: FastAPI, nl_client: AsyncClient, db_session: AsyncSession
) -> None:
    link = await new_link(nl_app, "ann@example.com")  # the service itself is untouched
    magic = await post(nl_client, "/api/v1/auth/magic-link", json={"email": "ann@example.com"})
    verify = await post(nl_client, "/api/v1/auth/verify", json={"token": token_of(link)})
    for response in (magic, verify):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
    assert await count(db_session, User) == 0
    assert await count(db_session, UserSession) == 0


async def test_meta_and_session_work_in_no_login_mode(nl_client: AsyncClient) -> None:
    assert (await nl_client.get("/api/v1/meta")).json()["no_login"] is True
    assert (await nl_client.get("/api/v1/auth/session")).json() == {
        "authenticated": False,
        "user": None,
    }


# -- husband ----------------------------------------------------------------------------------


async def test_husband_first_creates_the_household_once(
    nl_client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await choose(nl_client, "husband")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == HUSBAND_EMAIL
    assert body["user"]["display_name"] == "Husband"
    assert body["user"]["role"] == "owner"
    assert nl_client.cookies["session"]

    assert await household_counts(db_session) == {
        "User": 1,
        "Couple": 1,
        "CoupleMember": 1,
        "PartnerProfile": 2,
        "CoupleConstraints": 1,
    }
    (member,) = (await db_session.execute(select(CoupleMember))).scalars().all()
    assert member.role == "owner" and member.left_at is None
    profiles = {
        p.slot: p for p in (await db_session.execute(select(PartnerProfile))).scalars().all()
    }
    assert profiles["owner"].subject_user_id == member.user_id
    assert profiles["partner"].subject_user_id is None

    again = await choose(nl_client, "husband")
    assert again.status_code == 200
    assert again.json()["user"]["id"] == body["user"]["id"]
    assert await household_counts(db_session) == {
        "User": 1,
        "Couple": 1,
        "CoupleMember": 1,
        "PartnerProfile": 2,
        "CoupleConstraints": 1,
    }


# -- wife -------------------------------------------------------------------------------------


async def test_wife_first_creates_the_household_and_claims_the_partner_slot(
    nl_client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await choose(nl_client, "wife")
    assert response.status_code == 200
    user = response.json()["user"]
    assert user["email"] == WIFE_EMAIL
    assert user["display_name"] == "Wife"
    assert user["role"] == "partner"

    expected = {
        "User": 2,
        "Couple": 1,
        "CoupleMember": 2,
        "PartnerProfile": 2,
        "CoupleConstraints": 1,
    }
    assert await household_counts(db_session) == expected
    users = {u.email: u for u in (await db_session.execute(select(User))).scalars().all()}
    members = {
        m.user_id: m for m in (await db_session.execute(select(CoupleMember))).scalars().all()
    }
    assert members[users[HUSBAND_EMAIL].id].role == "owner"
    assert members[users[WIFE_EMAIL].id].role == "partner"
    assert users[HUSBAND_EMAIL].display_name == "Husband"
    profiles = {
        p.slot: p for p in (await db_session.execute(select(PartnerProfile))).scalars().all()
    }
    assert profiles["owner"].subject_user_id == users[HUSBAND_EMAIL].id
    assert profiles["partner"].subject_user_id == users[WIFE_EMAIL].id

    assert (await choose(nl_client, "wife")).status_code == 200
    assert await household_counts(db_session) == expected


async def test_husband_after_wife_and_wife_after_husband_share_one_household(
    nl_app: FastAPI, db_session: AsyncSession
) -> None:
    async with browser(nl_app) as husband_browser, browser(nl_app) as wife_browser:
        husband = (await choose(husband_browser, "husband")).json()["user"]
        # Before the wife exists the partner slot is unclaimed.
        (partner,) = (
            (
                await db_session.execute(
                    select(PartnerProfile).where(PartnerProfile.slot == "partner")
                )
            )
            .scalars()
            .all()
        )
        assert partner.subject_user_id is None
        wife = (await choose(wife_browser, "wife")).json()["user"]

        assert wife["couple_id"] == husband["couple_id"]
        assert (husband["role"], wife["role"]) == ("owner", "partner")
        assert (await husband_browser.get("/api/v1/me")).json() == husband
        assert (await wife_browser.get("/api/v1/me")).json() == wife
    assert (await household_counts(db_session))["Couple"] == 1
    await db_session.refresh(partner)
    assert partner.subject_user_id == uuid.UUID(wife["id"])


async def test_display_names_are_never_overwritten_by_a_later_choice(
    nl_client: AsyncClient, db_session: AsyncSession
) -> None:
    await choose(nl_client, "husband")
    await choose(nl_client, "wife")
    await db_session.execute(
        User.__table__.update().values(display_name="Custom")  # type: ignore[attr-defined]
    )
    await db_session.commit()
    await choose(nl_client, "husband")
    await choose(nl_client, "wife")
    names = {u.display_name for u in (await db_session.execute(select(User))).scalars().all()}
    assert names == {"Custom"}


# -- races ------------------------------------------------------------------------------------


@pytest.mark.parametrize("profiles", [("husband",) * 3, ("wife",) * 3, ("husband", "wife") * 2])
async def test_concurrent_first_choices_make_one_household(
    nl_app: FastAPI, db_session: AsyncSession, profiles: tuple[str, ...]
) -> None:
    clients = [browser(nl_app) for _ in profiles]
    entered = [await c.__aenter__() for c in clients]
    try:
        results = await asyncio.gather(
            *(choose(c, p) for c, p in zip(entered, profiles, strict=True))
        )
    finally:
        for c in clients:
            await c.__aexit__(None, None, None)
    assert [r.status_code for r in results] == [200] * len(profiles)
    counts = await household_counts(db_session)
    assert counts["Couple"] == 1
    assert counts["CoupleConstraints"] == 1
    assert counts["PartnerProfile"] == 2
    assert counts["User"] == (2 if "wife" in profiles else 1)
    assert counts["CoupleMember"] == counts["User"]
    assert len({r.json()["user"]["couple_id"] for r in results}) == 1


# -- sessions ---------------------------------------------------------------------------------


async def test_switching_profile_revokes_the_old_session(
    nl_client: AsyncClient, db_session: AsyncSession
) -> None:
    await choose(nl_client, "husband")
    husband_cookie = nl_client.cookies["session"]
    switched = await choose(nl_client, "wife")
    assert switched.json()["user"]["role"] == "partner"
    wife_cookie = nl_client.cookies["session"]
    assert wife_cookie != husband_cookie

    rows = (await db_session.execute(select(UserSession))).scalars().all()
    assert sorted(r.revoked_at is None for r in rows) == [False, True]

    nl_client.cookies.set("session", husband_cookie)
    old = await nl_client.get("/api/v1/me")
    assert old.status_code == 401
    assert old.json()["error"]["code"] == "unauthenticated"
    nl_client.cookies.set("session", wife_cookie)
    assert (await nl_client.get("/api/v1/me")).json()["role"] == "partner"


async def test_choosing_again_as_the_same_profile_replaces_the_session_too(
    nl_client: AsyncClient,
) -> None:
    await choose(nl_client, "husband")
    first = nl_client.cookies["session"]
    await choose(nl_client, "husband")
    nl_client.cookies.set("session", first)
    assert (await nl_client.get("/api/v1/me")).status_code == 401


async def test_logout_and_logout_all_still_work(nl_app: FastAPI, nl_client: AsyncClient) -> None:
    await choose(nl_client, "husband")
    assert (await post(nl_client, "/api/v1/auth/logout")).status_code == 204
    assert (await nl_client.get("/api/v1/me")).status_code == 401
    async with browser(nl_app) as other:
        await choose(other, "wife")
        await choose(nl_client, "wife")
        assert (await post(nl_client, "/api/v1/auth/logout-all")).status_code == 204
        assert (await other.get("/api/v1/me")).status_code == 401


# -- negatives --------------------------------------------------------------------------------


async def test_choose_profile_without_a_csrf_header_is_403(
    nl_client: AsyncClient, db_session: AsyncSession
) -> None:
    await nl_client.get("/api/v1/meta")  # holds the cookie, sends no header
    response = await nl_client.post(CHOOSE, json={"profile": "husband"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"
    assert await count(db_session, User) == 0


@pytest.mark.parametrize("profile", ["", "Husband", "admin", "owner", None, 1])
async def test_a_bad_profile_value_is_422(
    nl_client: AsyncClient, db_session: AsyncSession, profile: Any
) -> None:
    response = await post(nl_client, CHOOSE, json={"profile": profile})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert await count(db_session, User) == 0


async def test_choose_profile_shares_the_verify_ip_limit(nl_client: AsyncClient) -> None:
    for _ in range(30):
        assert (await choose(nl_client, "husband")).status_code == 200
    response = await choose(nl_client, "husband")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert int(response.headers["retry-after"]) > 0


# -- gating order -----------------------------------------------------------------------------


async def test_email_routes_are_404_before_the_email_provider_is_even_resolved(
    db_schema: str,
) -> None:
    # Resend without credentials makes get_email_sender raise 503; the gate must win.
    app = create_app(
        make_settings(
            database_url=db_schema, no_login=True, demo_mode=False, email_provider="resend"
        )
    )
    async with browser(app) as client:
        magic = await post(client, "/api/v1/auth/magic-link", json={"email": "ann@example.com"})
        malformed = await post(client, "/api/v1/auth/magic-link", json={"nope": 1})
        verify = await post(client, "/api/v1/auth/verify", json={"token": "x"})
    for response in (magic, malformed, verify):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


# -- reserved household addresses -------------------------------------------------------------


@pytest.mark.parametrize("email", [HUSBAND_EMAIL, WIFE_EMAIL, "Husband@Household.Invalid"])
async def test_household_addresses_cannot_request_a_magic_link(
    db_client: AsyncClient, db_session: AsyncSession, email: str
) -> None:
    response = await post(db_client, "/api/v1/auth/magic-link", json={"email": email})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert await count(db_session, MagicLinkToken) == 0


async def test_a_token_for_a_household_address_cannot_be_verified(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add(
        MagicLinkToken(
            email=HUSBAND_EMAIL,
            token_hash=hash_token("planted"),
            expires_at=func.now() + timedelta(minutes=10),
        )
    )
    await db_session.commit()
    response = await post(db_client, "/api/v1/auth/verify", json={"token": "planted"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_token"
    assert await count(db_session, User) == 0
    assert await count(db_session, UserSession) == 0
    assert "session" not in db_client.cookies


# -- household invariants ---------------------------------------------------------------------


async def make_couple_with(
    db: AsyncSession, owner: User, partner: User | None = None, owner_subject: User | None = None
) -> Couple:
    """A couple the way sign-up makes one: owner member, both slots, constraints."""
    couple = Couple()
    db.add(couple)
    await db.flush()
    db.add(CoupleMember(couple_id=couple.id, user_id=owner.id, role="owner"))
    if partner is not None:
        db.add(CoupleMember(couple_id=couple.id, user_id=partner.id, role="partner"))
    subject_id = owner_subject.id if owner_subject else None
    db.add(PartnerProfile(couple_id=couple.id, slot="owner", subject_user_id=subject_id))
    db.add(PartnerProfile(couple_id=couple.id, slot="partner", subject_user_id=None))
    db.add(CoupleConstraints(couple_id=couple.id))
    await db.flush()
    return couple


async def new_user(db: AsyncSession, email: str) -> User:
    user = User(email=email)
    db.add(user)
    await db.flush()
    return user


async def snapshot(db: AsyncSession) -> dict[str, int]:
    return {**await household_counts(db), "UserSession": await count(db, UserSession)}


async def partner_slot(db: AsyncSession) -> PartnerProfile:
    await db.rollback()
    return (
        await db.execute(select(PartnerProfile).where(PartnerProfile.slot == "partner"))
    ).scalar_one()


def assert_household_conflict(response: Any) -> None:
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "household_conflict"
    assert "@" not in error["message"]


async def test_husband_who_does_not_hold_the_owner_slot_is_a_409(
    nl_client: AsyncClient, db_session: AsyncSession
) -> None:
    husband = await new_user(db_session, HUSBAND_EMAIL)
    stranger = await new_user(db_session, "stranger@example.com")
    await make_couple_with(db_session, husband, owner_subject=stranger)
    await db_session.commit()
    before = await snapshot(db_session)
    assert_household_conflict(await choose(nl_client, "husband"))
    assert_household_conflict(await choose(nl_client, "wife"))
    assert await snapshot(db_session) == before
    assert "session" not in nl_client.cookies


async def test_husband_who_is_only_a_partner_member_is_a_409(
    nl_client: AsyncClient, db_session: AsyncSession
) -> None:
    husband = await new_user(db_session, HUSBAND_EMAIL)
    owner = await new_user(db_session, "owner@example.com")
    await make_couple_with(db_session, owner, partner=husband, owner_subject=owner)
    await db_session.commit()
    before = await snapshot(db_session)
    assert_household_conflict(await choose(nl_client, "husband"))
    assert await snapshot(db_session) == before


async def test_wife_who_belongs_to_a_different_couple_is_a_409_and_nothing_is_created(
    nl_app: FastAPI, nl_client: AsyncClient, db_session: AsyncSession
) -> None:
    assert (await choose(nl_client, "husband")).status_code == 200
    wife = await new_user(db_session, WIFE_EMAIL)
    elsewhere = await new_user(db_session, "elsewhere@example.com")
    await make_couple_with(db_session, elsewhere, partner=wife, owner_subject=elsewhere)
    await db_session.commit()
    before = await snapshot(db_session)
    async with browser(nl_app) as other:
        assert_household_conflict(await choose(other, "wife"))
    assert await snapshot(db_session) == before
    # The husband can still use his own household.
    assert (await choose(nl_client, "husband")).status_code == 200


async def test_a_partner_slot_held_by_someone_else_is_a_409_and_no_wife_persists(
    nl_client: AsyncClient, db_session: AsyncSession
) -> None:
    assert (await choose(nl_client, "husband")).status_code == 200
    other = await new_user(db_session, "other@example.com")
    other_id = other.id
    await db_session.execute(
        update(PartnerProfile)
        .where(PartnerProfile.slot == "partner")
        .values(subject_user_id=other.id)
    )
    await db_session.commit()
    before = await snapshot(db_session)
    assert_household_conflict(await choose(nl_client, "wife"))
    assert await snapshot(db_session) == before
    emails = {u.email for u in (await db_session.execute(select(User))).scalars().all()}
    assert WIFE_EMAIL not in emails
    assert (await partner_slot(db_session)).subject_user_id == other_id


async def test_a_missing_partner_slot_is_a_409(
    nl_client: AsyncClient, db_session: AsyncSession
) -> None:
    assert (await choose(nl_client, "husband")).status_code == 200
    await db_session.execute(delete(PartnerProfile).where(PartnerProfile.slot == "partner"))
    await db_session.commit()
    before = await snapshot(db_session)
    assert_household_conflict(await choose(nl_client, "wife"))
    assert await snapshot(db_session) == before


async def test_an_existing_wife_with_an_unclaimed_slot_claims_it(
    nl_client: AsyncClient, db_session: AsyncSession
) -> None:
    wife_id = (await choose(nl_client, "wife")).json()["user"]["id"]
    await db_session.execute(
        update(PartnerProfile).where(PartnerProfile.slot == "partner").values(subject_user_id=None)
    )
    await db_session.commit()
    assert (await choose(nl_client, "wife")).status_code == 200
    assert str((await partner_slot(db_session)).subject_user_id) == wife_id


async def test_a_household_conflict_logs_no_address(
    nl_client: AsyncClient, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    husband = await new_user(db_session, HUSBAND_EMAIL)
    await make_couple_with(db_session, husband)  # owner slot has no subject
    await db_session.commit()
    caplog.set_level(logging.DEBUG)
    assert_household_conflict(await choose(nl_client, "husband"))
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "household.invalid" not in json.dumps(errors[0].__dict__, default=str)


@pytest.mark.parametrize(
    "damage",
    ["slot_held_by_stranger", "constraints_missing", "extra_member", "partner_slot_missing"],
)
async def test_choosing_husband_validates_the_whole_household(
    nl_app: FastAPI, nl_client: AsyncClient, db_session: AsyncSession, damage: str
) -> None:
    assert (await choose(nl_client, "husband")).status_code == 200
    stranger = await new_user(db_session, "stranger@example.com")
    stranger_id = stranger.id
    if damage == "slot_held_by_stranger":
        await db_session.execute(
            update(PartnerProfile)
            .where(PartnerProfile.slot == "partner")
            .values(subject_user_id=stranger_id)
        )
    elif damage == "constraints_missing":
        await db_session.execute(delete(CoupleConstraints))
    elif damage == "extra_member":
        couple_id = (await db_session.execute(select(Couple.id))).scalar_one()
        db_session.add(CoupleMember(couple_id=couple_id, user_id=stranger_id, role="partner"))
    else:
        await db_session.execute(delete(PartnerProfile).where(PartnerProfile.slot == "partner"))
    await db_session.commit()
    before = await snapshot(db_session)
    async with browser(nl_app) as other:
        assert_household_conflict(await choose(other, "husband"))
        assert "session" not in other.cookies
    assert await snapshot(db_session) == before


async def test_choosing_husband_is_fine_when_the_wife_holds_the_partner_slot(
    nl_app: FastAPI, nl_client: AsyncClient, db_session: AsyncSession
) -> None:
    wife = (await choose(nl_client, "wife")).json()["user"]
    async with browser(nl_app) as other:
        response = await choose(other, "husband")
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "owner"
    assert response.json()["user"]["couple_id"] == wife["couple_id"]
    assert str((await partner_slot(db_session)).subject_user_id) == wife["id"]


@pytest.mark.parametrize("where", ["no_membership", "other_couple"])
async def test_choosing_husband_is_a_409_when_the_slot_holder_is_not_a_member(
    nl_app: FastAPI, nl_client: AsyncClient, db_session: AsyncSession, where: str
) -> None:
    wife_id = uuid.UUID((await choose(nl_client, "wife")).json()["user"]["id"])
    await db_session.execute(delete(CoupleMember).where(CoupleMember.user_id == wife_id))
    if where == "other_couple":
        wife = (await db_session.execute(select(User).where(User.id == wife_id))).scalar_one()
        elsewhere = await new_user(db_session, "elsewhere@example.com")
        await make_couple_with(db_session, elsewhere, partner=wife, owner_subject=elsewhere)
    await db_session.commit()
    before = await snapshot(db_session)
    async with browser(nl_app) as other:
        assert_household_conflict(await choose(other, "husband"))
        assert "session" not in other.cookies
    assert await snapshot(db_session) == before


async def test_reserved_address_is_422_even_when_the_email_provider_is_unavailable(
    db_schema: str,
) -> None:
    app = create_app(
        make_settings(database_url=db_schema, demo_mode=False, email_provider="resend")
    )
    async with browser(app) as client:
        reserved = await post(client, "/api/v1/auth/magic-link", json={"email": WIFE_EMAIL})
        ordinary = await post(client, "/api/v1/auth/magic-link", json={"email": "a@example.com"})
    assert reserved.status_code == 422
    assert reserved.json()["error"]["code"] == "validation_error"
    assert ordinary.status_code == 503  # the provider really is unavailable


async def test_choose_profile_is_404_before_validation_when_no_login_is_off(
    db_client: AsyncClient,
) -> None:
    for body in ({"profile": "nonsense"}, {}, {"profile": None}):
        response = await post(db_client, CHOOSE, json=body)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
