"""Pairing (FR-PAIR-1..3): invites, join, leave and the couple.purge job, against the real
database. Email-login mode only: the routes are 404 in NO_LOGIN mode."""

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import create_app
from app.models import (
    Couple,
    CoupleConstraints,
    CoupleMember,
    Idea,
    Invite,
    Job,
    PartnerProfile,
)
from app.security.ratelimit import SlidingWindowLimiter
from app.security.tokens import hash_token
from app.services.pairing import SHORT_CODE_ALPHABET
from app.worker import handlers
from tests.authutil import add_partner, browser, call, sign_in, token_of
from tests.conftest import make_settings
from tests.test_choose_profile import choose

pytestmark = pytest.mark.db

COUPLE = "/api/v1/couple"
INVITES = f"{COUPLE}/invites"
CURRENT = f"{INVITES}/current"
JOIN = f"{COUPLE}/join"
LEAVE = f"{COUPLE}/leave"
ME = "/api/v1/me"


async def create_invite(owner: AsyncClient) -> dict[str, Any]:
    response = await call(owner, "POST", INVITES)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def join_with(client: AsyncClient, **body: str) -> Response:
    return await call(client, "POST", JOIN, body)


async def me(client: AsyncClient) -> dict[str, Any]:
    response = await client.get(ME)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


@asynccontextmanager
async def paired(
    app: FastAPI, owner_email: str = "ann@example.com", joiner_email: str = "bea@example.com"
) -> AsyncIterator[tuple[AsyncClient, AsyncClient]]:
    """Two browsers that are a couple, paired through the invite flow."""
    async with browser(app) as owner, browser(app) as joiner:
        await sign_in(app, owner, owner_email)
        await sign_in(app, joiner, joiner_email)
        invite = await create_invite(owner)
        response = await join_with(joiner, token=token_of(invite["link"]))
        assert response.status_code == 200, response.text
        yield owner, joiner


async def fresh(db: AsyncSession, model: Any, key: uuid.UUID | str) -> Any:
    return await db.get(model, key, populate_existing=True)


async def active_membership(db: AsyncSession, user_id: str) -> CoupleMember:
    await db.rollback()
    return (
        await db.execute(
            select(CoupleMember).where(
                CoupleMember.user_id == uuid.UUID(user_id), CoupleMember.left_at.is_(None)
            )
        )
    ).scalar_one()


async def purge_job(db: AsyncSession, couple_id: uuid.UUID | str) -> Job | None:
    await db.rollback()
    return (
        await db.execute(select(Job).where(Job.dedupe_key == f"couple.purge:{couple_id}"))
    ).scalar_one_or_none()


async def run_purge(app: FastAPI, payload: dict[str, Any]) -> None:
    async with app.state.sessionmaker() as session:
        await handlers.couple_purge(session, payload)
        await session.commit()


# --- NO_LOGIN


async def test_every_pairing_route_is_404_in_no_login_mode(db_schema: str) -> None:
    app = create_app(make_settings(database_url=db_schema, no_login=True))
    async with browser(app) as husband:
        assert (await choose(husband, "husband")).status_code == 200
        for method, url, body in (
            ("POST", INVITES, None),
            ("GET", CURRENT, None),
            ("DELETE", CURRENT, None),
            ("POST", JOIN, {"short_code": "ABCDEFGH"}),
            ("POST", LEAVE, None),
        ):
            response = await call(husband, method, url, body)
            assert response.status_code == 404, f"{method} {url}: {response.text}"
            assert response.json()["error"]["code"] == "not_found"


# --- Invites


async def test_owner_creates_an_invite_and_the_token_is_never_stored(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with browser(db_app) as owner:
        await sign_in(db_app, owner, "ann@example.com")
        invite = await create_invite(owner)
        token = token_of(invite["link"])
        assert invite["link"] == f"http://localhost:8080/join#token={token}"
        assert len(invite["short_code"]) == 8
        assert set(invite["short_code"]) <= set(SHORT_CODE_ALPHABET)
        expires = dt.datetime.fromisoformat(invite["expires_at"])
        assert (
            dt.timedelta(days=6, hours=23)
            < expires - dt.datetime.now(dt.UTC)
            < dt.timedelta(days=7, minutes=1)
        )

        rows = (await db_session.execute(select(Invite))).scalars().all()
        assert len(rows) == 1
        stored = rows[0]
        assert stored.token_hash == hash_token(token)
        assert len(stored.token_hash) == 64
        assert token not in (stored.token_hash, stored.short_code)
        assert stored.short_code == invite["short_code"]
        assert stored.created_by_user_id == uuid.UUID((await me(owner))["id"])

        current = await owner.get(CURRENT)
        assert current.status_code == 200
        assert current.json() == {
            "short_code": invite["short_code"],
            "expires_at": invite["expires_at"],
        }
        assert token not in current.text


async def test_only_the_owner_manages_invites(db_app: FastAPI) -> None:
    async with browser(db_app) as owner, browser(db_app) as partner:
        await sign_in(db_app, owner, "ann@example.com")
        await add_partner(db_app, "ann@example.com", "bea@example.com")
        await sign_in(db_app, partner, "bea@example.com")
        for method, url in (("POST", INVITES), ("GET", CURRENT), ("DELETE", CURRENT)):
            response = await call(partner, method, url)
            assert response.status_code == 403, f"{method} {url}: {response.text}"
            assert response.json()["error"]["code"] == "owner_only"


async def test_a_couple_with_a_partner_cannot_invite(db_app: FastAPI) -> None:
    async with paired(db_app) as (owner, _):
        response = await call(owner, "POST", INVITES)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "couple_full"


async def test_creating_an_invite_revokes_the_previous_one(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with browser(db_app) as owner, browser(db_app) as joiner:
        await sign_in(db_app, owner, "ann@example.com")
        await sign_in(db_app, joiner, "bea@example.com")
        first = await create_invite(owner)
        second = await create_invite(owner)
        assert first["short_code"] != second["short_code"]

        rows = {r.short_code: r for r in (await db_session.execute(select(Invite))).scalars().all()}
        assert rows[first["short_code"]].revoked_at is not None
        assert rows[second["short_code"]].revoked_at is None
        assert (await owner.get(CURRENT)).json()["short_code"] == second["short_code"]

        stale = await join_with(joiner, token=token_of(first["link"]))
        assert stale.status_code == 404
        assert stale.json()["error"]["code"] == "invite_invalid"
        assert (await join_with(joiner, short_code=second["short_code"])).status_code == 200


async def test_current_invite_is_404_without_an_active_one_and_delete_is_idempotent(
    db_app: FastAPI,
) -> None:
    async with browser(db_app) as owner:
        await sign_in(db_app, owner, "ann@example.com")
        missing = await owner.get(CURRENT)
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "no_invite"
        assert (await call(owner, "DELETE", CURRENT)).status_code == 204  # nothing to revoke

        await create_invite(owner)
        assert (await call(owner, "DELETE", CURRENT)).status_code == 204
        assert (await call(owner, "DELETE", CURRENT)).status_code == 204
        assert (await owner.get(CURRENT)).json()["error"]["code"] == "no_invite"


# --- Join


async def test_join_by_token_makes_the_joiner_the_partner(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with browser(db_app) as owner, browser(db_app) as joiner:
        await sign_in(db_app, owner, "ann@example.com")
        await sign_in(db_app, joiner, "bea@example.com")
        owner_me, joiner_before = await me(owner), await me(joiner)
        invite = await create_invite(owner)

        response = await join_with(joiner, token=token_of(invite["link"]))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == joiner_before["id"]
        assert body["couple_id"] == owner_me["couple_id"]
        assert body["role"] == "partner"
        assert await me(joiner) == body

        couple_id = uuid.UUID(owner_me["couple_id"])
        member = await active_membership(db_session, joiner_before["id"])
        assert (member.couple_id, member.role) == (couple_id, "partner")
        profile = (
            await db_session.execute(
                select(PartnerProfile).where(
                    PartnerProfile.couple_id == couple_id, PartnerProfile.slot == "partner"
                )
            )
        ).scalar_one()
        assert str(profile.subject_user_id) == joiner_before["id"]

        used = (await db_session.execute(select(Invite))).scalar_one()
        assert used.used_at is not None
        assert str(used.used_by_user_id) == joiner_before["id"]
        assert (await owner.get(CURRENT)).status_code == 404  # a used invite is not current

        # The joiner's solo couple is archived, its member left, and its purge is queued.
        solo_id = uuid.UUID(joiner_before["couple_id"])
        solo = await fresh(db_session, Couple, solo_id)
        assert solo.status == "archived"
        assert solo.archived_at is not None
        solo_member = (
            await db_session.execute(
                select(CoupleMember).where(
                    CoupleMember.couple_id == solo_id, CoupleMember.user_id == member.user_id
                )
            )
        ).scalar_one()
        assert solo_member.left_at is not None
        job = await purge_job(db_session, solo_id)
        assert job is not None
        assert job.kind == "couple.purge"
        assert job.payload == {"couple_id": str(solo_id)}
        assert job.status == "queued"
        delay = job.run_at - dt.datetime.now(dt.UTC)
        assert dt.timedelta(days=29, hours=23) < delay < dt.timedelta(days=30, minutes=1)

        # Both now see the same couple, with two members.
        couple = (await owner.get(COUPLE)).json()
        assert {m["role"] for m in couple["members"]} == {"owner", "partner"}
        assert couple["partner_joined"] is True


async def test_join_by_short_code_ignores_case_and_spaces(db_app: FastAPI) -> None:
    async with browser(db_app) as owner, browser(db_app) as joiner:
        await sign_in(db_app, owner, "ann@example.com")
        await sign_in(db_app, joiner, "bea@example.com")
        invite = await create_invite(owner)
        typed = f"  {invite['short_code'].lower()} "
        response = await join_with(joiner, short_code=typed)
        assert response.status_code == 200, response.text
        assert response.json()["role"] == "partner"
        assert response.json()["couple_id"] == (await me(owner))["couple_id"]


@pytest.mark.parametrize("body", [{}, {"token": "a", "short_code": "B"}, {"token": ""}])
async def test_join_needs_exactly_one_of_token_and_short_code(
    db_app: FastAPI, body: dict[str, str]
) -> None:
    async with browser(db_app) as joiner:
        await sign_in(db_app, joiner, "bea@example.com")
        response = await join_with(joiner, **body)
        assert response.status_code == 422, response.text


@pytest.mark.parametrize("how", ["token", "short_code"])
@pytest.mark.parametrize("state", ["unknown", "expired", "revoked", "used"])
async def test_an_inactive_invite_is_invite_invalid(
    db_app: FastAPI, db_session: AsyncSession, how: str, state: str
) -> None:
    async with browser(db_app) as owner, browser(db_app) as first, browser(db_app) as other:
        await sign_in(db_app, owner, "ann@example.com")
        await sign_in(db_app, first, "bea@example.com")
        await sign_in(db_app, other, "cy@example.com")
        invite = await create_invite(owner)
        secret = {"token": token_of(invite["link"]), "short_code": invite["short_code"]}
        if state == "expired":
            await db_session.execute(
                update(Invite).values(expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1))
            )
            await db_session.commit()
        elif state == "revoked":
            assert (await call(owner, "DELETE", CURRENT)).status_code == 204
        elif state == "used":
            assert (await join_with(first, **{how: secret[how]})).status_code == 200
        elif how == "token":
            secret = {"token": "not-a-real-token"}
        else:
            secret = {"short_code": "AAAAAAAA"}

        response = await join_with(other, **{how: secret[how]})
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "invite_invalid"
        # Nothing moved: the joiner is still alone in their own active couple.
        after = await me(other)
        assert after["role"] == "owner"
        assert after["couple_id"] != (await me(owner))["couple_id"]


async def test_an_invite_from_an_owner_who_left_is_invite_invalid(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with browser(db_app) as owner, browser(db_app) as joiner:
        await sign_in(db_app, owner, "ann@example.com")
        await sign_in(db_app, joiner, "bea@example.com")
        invite = await create_invite(owner)
        # What account deletion does to the owner's membership.
        await db_session.execute(
            update(CoupleMember)
            .where(CoupleMember.couple_id == select(Invite.couple_id).scalar_subquery())
            .values(left_at=dt.datetime.now(dt.UTC))
        )
        await db_session.commit()
        response = await join_with(joiner, short_code=invite["short_code"])
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "invite_invalid"


async def test_joining_your_own_couple_is_already_member(db_app: FastAPI) -> None:
    async with browser(db_app) as owner:
        await sign_in(db_app, owner, "ann@example.com")
        invite = await create_invite(owner)
        response = await join_with(owner, short_code=invite["short_code"])
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "already_member"
        assert (await me(owner))["role"] == "owner"


async def test_joining_a_full_couple_is_couple_full(db_app: FastAPI) -> None:
    async with browser(db_app) as owner, browser(db_app) as partner, browser(db_app) as late:
        await sign_in(db_app, owner, "ann@example.com")
        await sign_in(db_app, late, "cy@example.com")
        invite = await create_invite(owner)
        # A partner arrives some other way after the invite was issued.
        await add_partner(db_app, "ann@example.com", "bea@example.com")
        response = await join_with(late, token=token_of(invite["link"]))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "couple_full"
        assert (await me(late))["role"] == "owner"

        # A member redeeming their own couple's invite is already_member, not couple_full.
        await sign_in(db_app, partner, "bea@example.com")
        again = await join_with(partner, short_code=invite["short_code"])
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "already_member"


async def test_a_member_of_another_couple_is_already_paired(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with (
        paired(db_app, "bob@example.com", "cat@example.com") as (bob, cat),
        browser(db_app) as ann,
    ):
        await sign_in(db_app, ann, "ann@example.com")
        invite = await create_invite(ann)
        before = await me(cat)
        for member in (cat, bob):
            response = await join_with(member, token=token_of(invite["link"]))
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == "already_paired"
        assert await me(cat) == before
        assert (await ann.get(CURRENT)).status_code == 200  # the invite was not consumed
        couple = await fresh(db_session, Couple, uuid.UUID(before["couple_id"]))
        assert couple.status == "active"


async def test_join_is_rate_limited_per_ip(db_app: FastAPI) -> None:
    async with browser(db_app) as joiner:
        await sign_in(db_app, joiner, "bea@example.com")
        # sign_in used the same limiter; swap in a small fresh one for the join attempts.
        db_app.state.verify_ip_limiter = SlidingWindowLimiter(limit=2, window_sec=60)
        for _ in range(2):
            response = await join_with(joiner, short_code="AAAAAAAA")
            assert response.status_code == 404
        limited = await join_with(joiner, short_code="AAAAAAAA")
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "rate_limited"
        assert int(limited.headers["Retry-After"]) >= 1


# --- Leave


async def set_profile_text(db: AsyncSession, couple_id: str, slot: str, loves: str) -> None:
    await db.execute(
        update(PartnerProfile)
        .where(PartnerProfile.couple_id == uuid.UUID(couple_id), PartnerProfile.slot == slot)
        .values(
            loves=loves,
            display_name=f"{slot} name",
            liked_categories=["museums"],
            energy=4,
            confirmed_by_subject_at=func.now(),
        )
    )
    await db.commit()


async def test_leave_archives_the_couple_and_gives_each_member_a_fresh_solo_couple(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with paired(db_app) as (owner, partner):
        old = (await me(owner))["couple_id"]
        owner_id, partner_id = (await me(owner))["id"], (await me(partner))["id"]
        await set_profile_text(db_session, old, "owner", "OWNER loves")
        await set_profile_text(db_session, old, "partner", "PARTNER loves")
        idea = Idea(
            couple_id=uuid.UUID(old),
            created_by_user_id=uuid.UUID(owner_id),
            source="manual",
            visibility="shared",
            title="Shared picnic",
            pitch="x",
        )
        db_session.add(idea)
        await db_session.commit()
        idea_id = idea.id

        assert (await call(partner, "POST", LEAVE)).status_code == 204

        couple = await fresh(db_session, Couple, uuid.UUID(old))
        assert couple.status == "archived"
        assert couple.archived_at is not None
        old_members = (
            (
                await db_session.execute(
                    select(CoupleMember).where(CoupleMember.couple_id == uuid.UUID(old))
                )
            )
            .scalars()
            .all()
        )
        assert len(old_members) == 2
        assert all(m.left_at is not None for m in old_members)

        job = await purge_job(db_session, old)
        assert job is not None
        assert job.payload == {"couple_id": old}
        assert (
            dt.timedelta(days=29, hours=23)
            < job.run_at - dt.datetime.now(dt.UTC)
            < dt.timedelta(days=30, minutes=1)
        )

        new_ids = set()
        for client, user_id, text in (
            (owner, owner_id, "OWNER loves"),
            (partner, partner_id, "PARTNER loves"),
        ):
            session_me = await me(client)  # the same session now resolves to the new couple
            assert session_me["role"] == "owner"
            assert session_me["couple_id"] != old
            new_ids.add(session_me["couple_id"])
            new_id = uuid.UUID(session_me["couple_id"])
            new_couple = await fresh(db_session, Couple, new_id)
            assert new_couple.status == "active"
            profiles = {
                p.slot: p
                for p in (
                    await db_session.execute(
                        select(PartnerProfile).where(PartnerProfile.couple_id == new_id)
                    )
                )
                .scalars()
                .all()
            }
            assert str(profiles["owner"].subject_user_id) == user_id
            assert profiles["owner"].loves == text
            assert profiles["owner"].liked_categories == ["museums"]
            assert profiles["owner"].energy == 4
            assert profiles["owner"].confirmed_by_subject_at is not None
            assert profiles["partner"].subject_user_id is None
            assert profiles["partner"].loves is None
            assert (
                await db_session.execute(
                    select(CoupleConstraints.couple_id).where(CoupleConstraints.couple_id == new_id)
                )
            ).first() is not None
            assert (await client.get(COUPLE)).json()["partner_joined"] is False
        assert len(new_ids) == 2

        # The old couple's shared data is still there until the purge.
        assert await fresh(db_session, Idea, idea_id) is not None
        # A second leave, now from a solo couple, changes nothing.
        before = await me(owner)
        assert (await call(owner, "POST", LEAVE)).status_code == 204
        assert await me(owner) == before
        assert (await purge_job(db_session, before["couple_id"])) is None


async def test_leave_revokes_the_couples_open_invites(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with paired(db_app) as (owner, partner):
        old = uuid.UUID((await me(owner))["couple_id"])
        db_session.add(
            Invite(
                couple_id=old,
                created_by_user_id=uuid.UUID((await me(owner))["id"]),
                token_hash=hash_token("leftover"),
                short_code="LEFTOVER",
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
            )
        )
        await db_session.commit()
        assert (await call(owner, "POST", LEAVE)).status_code == 204
        leftover = (
            await db_session.execute(select(Invite).where(Invite.short_code == "LEFTOVER"))
        ).scalar_one()
        await db_session.refresh(leftover)
        assert leftover.revoked_at is not None
        async with browser(db_app) as stranger:
            await sign_in(db_app, stranger, "cy@example.com")
            response = await join_with(stranger, token="leftover")
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "invite_invalid"


async def test_a_new_partner_does_not_inherit_a_deleted_partners_profile(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with paired(db_app) as (owner, partner):
        couple_id = uuid.UUID((await me(owner))["couple_id"])
        partner_id = (await me(partner))["id"]
        await db_session.execute(
            update(PartnerProfile)
            .where(PartnerProfile.couple_id == couple_id, PartnerProfile.slot == "partner")
            .values(loves="the old partner's secret")
        )
        await db_session.commit()
        deleted = await call(partner, "DELETE", f"{ME}?user_id={partner_id}")
        assert deleted.status_code == 200, deleted.text
        invite = await create_invite(owner)
        async with browser(db_app) as newcomer:
            await sign_in(db_app, newcomer, "cy@example.com")
            response = await join_with(newcomer, token=token_of(invite["link"]))
            assert response.status_code == 200, response.text
            newcomer_id = uuid.UUID((await me(newcomer))["id"])
        profile = (
            await db_session.execute(
                select(PartnerProfile)
                .where(PartnerProfile.couple_id == couple_id, PartnerProfile.slot == "partner")
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert profile.subject_user_id == newcomer_id
        assert profile.loves != "the old partner's secret"


async def test_a_partner_left_alone_can_still_leave(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with paired(db_app) as (owner, partner):
        old = uuid.UUID((await me(partner))["couple_id"])
        # What the owner's account deletion does to their membership.
        await db_session.execute(
            update(CoupleMember)
            .where(CoupleMember.couple_id == old, CoupleMember.role == "owner")
            .values(left_at=dt.datetime.now(dt.UTC))
        )
        await db_session.commit()
        assert (await call(partner, "POST", LEAVE)).status_code == 204
        after = await me(partner)
        assert after["role"] == "owner"
        assert uuid.UUID(after["couple_id"]) != old
        couple = await db_session.get(Couple, old, populate_existing=True)
        assert couple is not None and couple.status == "archived"


async def test_after_leaving_each_can_pair_again(db_app: FastAPI) -> None:
    async with paired(db_app) as (owner, partner):
        assert (await call(owner, "POST", LEAVE)).status_code == 204
        invite = await create_invite(owner)  # the new solo couple's owner can invite again
        response = await join_with(partner, token=token_of(invite["link"]))
        assert response.status_code == 200, response.text
        assert response.json()["couple_id"] == (await me(owner))["couple_id"]


# --- couple.purge


async def test_purge_deletes_a_still_archived_couple_and_spares_the_rest(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with paired(db_app) as (owner, partner):
        old = (await me(owner))["couple_id"]
        assert (await call(partner, "POST", LEAVE)).status_code == 204
        new_owner_couple = (await me(owner))["couple_id"]
        new_partner_couple = (await me(partner))["couple_id"]

        # Active again (re-paired): the purge leaves it alone.
        await db_session.execute(
            update(Couple).where(Couple.id == uuid.UUID(old)).values(status="active")
        )
        await db_session.commit()
        await run_purge(db_app, {"couple_id": old})
        assert await fresh(db_session, Couple, uuid.UUID(old)) is not None

        # Archived: gone, with its members and profiles; the members' new couples are not.
        await db_session.execute(
            update(Couple).where(Couple.id == uuid.UUID(old)).values(status="archived")
        )
        await db_session.commit()
        await run_purge(db_app, {"couple_id": old})
        await run_purge(db_app, {"couple_id": old})  # a second run no-ops
        await db_session.rollback()
        assert await fresh(db_session, Couple, uuid.UUID(old)) is None
        for table in (CoupleMember, PartnerProfile):
            left = (
                await db_session.execute(
                    select(func.count()).select_from(table).where(table.couple_id == uuid.UUID(old))
                )
            ).scalar_one()
            assert left == 0
        assert await fresh(db_session, Couple, uuid.UUID(new_owner_couple)) is not None
        assert await fresh(db_session, Couple, uuid.UUID(new_partner_couple)) is not None
        assert (await me(owner))["couple_id"] == new_owner_couple


async def test_purge_ignores_a_bad_payload_and_an_unknown_couple(db_app: FastAPI) -> None:
    await run_purge(db_app, {})
    await run_purge(db_app, {"couple_id": "not-a-uuid"})
    await run_purge(db_app, {"couple_id": str(uuid.uuid4())})
