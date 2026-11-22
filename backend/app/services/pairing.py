"""Pairing: invites, joining a couple and leaving it (FR-PAIR-1..3). Email-login mode only.

An invite's token is shown once and only its sha256 hash is stored. The short code is the typed
fallback: it is stored as is (the column is too short for a hash), which is acceptable because
it has 32**8 values, expires in 7 days and is only redeemed through a rate-limited route.
Nothing here logs a token, a short code or a link.

Every change to a couple's membership takes a per-couple advisory lock first, so two joins, or a
join and a leave, never interleave. A join locks two couples; it always takes them in id order.
Functions flush but leave the commit to the caller.
"""

import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import ColumnElement

from app.config import Settings
from app.errors import ApiError
from app.models import (
    Couple,
    CoupleConstraints,
    CoupleMember,
    Invite,
    PartnerProfile,
    User,
)
from app.schemas.auth import MeOut
from app.schemas.pairing import InviteCurrentOut, InviteOut, JoinIn
from app.security.tokens import hash_token, new_token
from app.services.account import delete_personal_data
from app.services.auth import me_out, membership
from app.services.couple import now, require_couple_id
from app.worker.jobs import enqueue

COUPLE_PURGE = "couple.purge"
PURGE_DELAY = timedelta(days=30)  # from an archived couple to the deletion of its data
INVITE_TTL = timedelta(days=7)
# No 0/O/1/I: a code read off a phone screen and typed in must not be ambiguous.
SHORT_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SHORT_CODE_LENGTH = 8

# The profile fields that make up a person's own profile, copied into their new solo couple.
_PROFILE_FIELDS = (
    "display_name",
    "liked_categories",
    "disliked_categories",
    "cuisines_liked",
    "cuisines_disliked",
    "energy",
    "indoor_outdoor",
    "social_setting",
    "loves",
    "hard_nos",
    "dietary",
    "allergies",
    "accessibility",
    "confirmed_by_subject_at",
)


def purge_key(couple_id: uuid.UUID) -> str:
    return f"{COUPLE_PURGE}:{couple_id}"


def _owner_only() -> ApiError:
    return ApiError(403, "owner_only", "Only the owner of the couple can do that.")


def _invite_invalid() -> ApiError:
    return ApiError(404, "invite_invalid", "This invitation is invalid or has expired.")


async def lock_couples(session: AsyncSession, *couple_ids: uuid.UUID) -> None:
    """Take the advisory locks of these couples, in id order, until the transaction ends."""
    for couple_id in sorted(set(couple_ids)):
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"couple:{couple_id}"},
        )


def _active_invite(*conditions: ColumnElement[bool]) -> tuple[ColumnElement[bool], ...]:
    """The WHERE conditions for an invite that is unused, unrevoked and unexpired."""
    return (
        Invite.used_at.is_(None),
        Invite.revoked_at.is_(None),
        Invite.expires_at > func.now(),
        *conditions,
    )


async def _active_partner(session: AsyncSession, couple_id: uuid.UUID) -> bool:
    return (
        await session.execute(
            select(CoupleMember.id).where(
                CoupleMember.couple_id == couple_id,
                CoupleMember.role == "partner",
                CoupleMember.left_at.is_(None),
            )
        )
    ).first() is not None


async def _revoke_active_invites(session: AsyncSession, couple_id: uuid.UUID) -> None:
    await session.execute(
        update(Invite)
        .where(
            Invite.couple_id == couple_id,
            Invite.used_at.is_(None),
            Invite.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
    )


def _require_owner(couple_id: uuid.UUID | None, role: str | None) -> uuid.UUID:
    couple_id = require_couple_id(couple_id)
    if role != "owner":
        raise _owner_only()
    return couple_id


async def _new_short_code(session: AsyncSession) -> str:
    """A code no invite, past or present, has used (the column is unique)."""
    while True:
        code = "".join(secrets.choice(SHORT_CODE_ALPHABET) for _ in range(SHORT_CODE_LENGTH))
        taken = (await session.execute(select(Invite.id).where(Invite.short_code == code))).first()
        if taken is None:
            return code


async def create_invite(
    session: AsyncSession,
    settings: Settings,
    user: User,
    couple_id: uuid.UUID | None,
    role: str | None,
) -> InviteOut:
    """Revoke the couple's active invite and issue a fresh one. The caller commits."""
    couple_id = _require_owner(couple_id, role)
    await lock_couples(session, couple_id)
    # The membership was resolved before the lock: a leave may have archived the couple since.
    couple = await session.get(Couple, couple_id, populate_existing=True)
    still_owner = (
        await session.execute(
            select(CoupleMember.id).where(
                CoupleMember.couple_id == couple_id,
                CoupleMember.user_id == user.id,
                CoupleMember.role == "owner",
                CoupleMember.left_at.is_(None),
            )
        )
    ).first()
    if couple is None or couple.status != "active" or still_owner is None:
        raise ApiError(409, "couple_changed", "Your couple changed. Reload and try again.")
    if await _active_partner(session, couple_id):
        raise ApiError(409, "couple_full", "Your couple already has a partner.")
    await _revoke_active_invites(session, couple_id)
    token = new_token()
    invite = Invite(
        couple_id=couple_id,
        created_by_user_id=user.id,
        token_hash=hash_token(token),
        short_code=await _new_short_code(session),
        expires_at=now() + INVITE_TTL,
    )
    session.add(invite)
    await session.flush()
    return InviteOut(
        link=f"{settings.origin}/join#token={token}",
        short_code=invite.short_code,
        expires_at=invite.expires_at,
    )


async def current_invite(
    session: AsyncSession, couple_id: uuid.UUID | None, role: str | None
) -> InviteCurrentOut:
    couple_id = _require_owner(couple_id, role)
    invite = (
        await session.execute(
            select(Invite)
            .where(*_active_invite(Invite.couple_id == couple_id))
            .order_by(Invite.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if invite is None:
        raise ApiError(404, "no_invite", "There is no active invitation.")
    return InviteCurrentOut(short_code=invite.short_code, expires_at=invite.expires_at)


async def revoke_current_invite(
    session: AsyncSession, couple_id: uuid.UUID | None, role: str | None
) -> None:
    """Idempotent: with no active invite there is nothing to do. The caller commits."""
    couple_id = _require_owner(couple_id, role)
    await _revoke_active_invites(session, couple_id)


async def _find_invite(session: AsyncSession, body: JoinIn, *, lock: bool) -> Invite | None:
    if body.token is not None:
        match = Invite.token_hash == hash_token(body.token)
    else:
        assert body.short_code is not None
        match = Invite.short_code == body.short_code.upper()
    stmt = select(Invite).where(*_active_invite(match))
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def _archive_couple(session: AsyncSession, couple: Couple) -> None:
    """Archive `couple` and queue the deletion of its data at +30 days."""
    couple.status = "archived"
    couple.archived_at = now()
    await _revoke_active_invites(session, couple.id)
    await enqueue(
        session,
        COUPLE_PURGE,
        {"couple_id": str(couple.id)},
        run_at=now() + PURGE_DELAY,
        dedupe_key=purge_key(couple.id),
    )


async def join(session: AsyncSession, user: User, body: JoinIn) -> MeOut:
    """Join the couple an invite belongs to, as its partner. One transaction; the caller
    commits."""
    invite = await _find_invite(session, body, lock=False)
    if invite is None:
        raise _invite_invalid()
    target_id = invite.couple_id
    own_id, _ = await membership(session, user.id)
    await lock_couples(session, target_id, *([own_id] if own_id else []))
    # Everything below is read again under the locks: the invite may have been redeemed or
    # revoked and the memberships may have changed while this request waited.
    invite = await _find_invite(session, body, lock=True)
    target = await session.get(Couple, target_id, populate_existing=True)
    if invite is None or target is None or target.status != "active":
        raise _invite_invalid()
    # An invite from an owner who has since deleted their account must not be redeemable.
    owner = (
        await session.execute(
            select(CoupleMember.id).where(
                CoupleMember.couple_id == target_id,
                CoupleMember.role == "owner",
                CoupleMember.left_at.is_(None),
            )
        )
    ).first()
    if owner is None:
        raise _invite_invalid()
    # Account deletion updates this row before closing the user's memberships, so locking it
    # orders a join and a deletion of the same user: either deletion sees the new membership
    # and closes it, or the join sees deleted_at and stops.
    deleted_at = (
        await session.execute(select(User.deleted_at).where(User.id == user.id).with_for_update())
    ).scalar_one_or_none()
    if deleted_at is not None:
        raise ApiError(401, "unauthenticated", "Please sign in to continue.")
    locked_own_id = own_id
    own_id, _ = await membership(session, user.id)
    if own_id != locked_own_id:
        # The joiner's couple changed while waiting, so the lock above covers the wrong one.
        raise ApiError(409, "couple_changed", "Your couple changed. Reload and try again.")
    if own_id == target_id:
        raise ApiError(409, "already_member", "You are already in this couple.")
    if await _active_partner(session, target_id):
        raise ApiError(409, "couple_full", "This couple already has a partner.")
    own_couple: Couple | None = None
    if own_id is not None:
        others = (
            await session.execute(
                select(CoupleMember.id).where(
                    CoupleMember.couple_id == own_id,
                    CoupleMember.user_id != user.id,
                    CoupleMember.left_at.is_(None),
                )
            )
        ).first()
        if others is not None:
            raise ApiError(409, "already_paired", "Leave your current couple before joining.")
        own_couple = await session.get(Couple, own_id, populate_existing=True)
    if own_couple is not None:
        await session.execute(
            update(CoupleMember)
            .where(
                CoupleMember.couple_id == own_couple.id,
                CoupleMember.user_id == user.id,
                CoupleMember.left_at.is_(None),
            )
            .values(left_at=func.now())
        )
        await _archive_couple(session, own_couple)
    session.add(CoupleMember(couple_id=target_id, user_id=user.id, role="partner"))
    previous = (
        await session.execute(
            select(PartnerProfile.subject_user_id).where(
                PartnerProfile.couple_id == target_id, PartnerProfile.slot == "partner"
            )
        )
    ).scalar_one_or_none()
    if previous is not None and previous != user.id:
        # Only a partner who deleted their account leaves an active couple's slot behind. Erase
        # their data now: once the slot is reassigned their purge job can no longer find it.
        await delete_personal_data(session, previous, target_id, keep_name=False)
    await session.execute(
        update(PartnerProfile)
        .where(PartnerProfile.couple_id == target_id, PartnerProfile.slot == "partner")
        .values(subject_user_id=user.id)
    )
    invite.used_at = now()
    invite.used_by_user_id = user.id
    await session.flush()
    return me_out(user, target_id, "partner")


async def _new_solo_couple(
    session: AsyncSession, user: User, old_profile: PartnerProfile | None
) -> None:
    """A fresh couple space with `user` as its owner, as at sign-up, carrying a copy of their
    own profile from the couple they just left."""
    couple = Couple(timezone=user.timezone, status="active")
    session.add(couple)
    await session.flush()
    own = PartnerProfile(couple_id=couple.id, slot="owner", subject_user_id=user.id)
    if old_profile is not None:
        for field in _PROFILE_FIELDS:
            setattr(own, field, getattr(old_profile, field))
    elif user.display_name:
        own.display_name = user.display_name
    session.add_all(
        [
            CoupleMember(couple_id=couple.id, user_id=user.id, role="owner"),
            own,
            PartnerProfile(couple_id=couple.id, slot="partner", subject_user_id=None),
            CoupleConstraints(couple_id=couple.id),
        ]
    )
    await session.flush()


async def leave(session: AsyncSession, user_id: uuid.UUID, couple_id: uuid.UUID | None) -> None:
    """End the pairing: archive the couple, give each member a fresh solo couple and queue the
    purge of the old one. A no-op if the caller is not in a two-member active couple (a second
    leave racing the first, or a solo member). The caller commits."""
    couple_id = require_couple_id(couple_id)
    await lock_couples(session, couple_id)
    couple = await session.get(Couple, couple_id, populate_existing=True)
    members = (
        (
            await session.execute(
                select(CoupleMember).where(
                    CoupleMember.couple_id == couple_id, CoupleMember.left_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    mine = next((m for m in members if m.user_id == user_id), None)
    # A partner left alone (the owner deleted their account) can still leave; a solo owner has
    # nothing to leave.
    if (
        couple is None
        or couple.status != "active"
        or mine is None
        or (len(members) < 2 and mine.role != "partner")
    ):
        return
    left_at: datetime = now()
    for member in members:
        member.left_at = left_at
    await _archive_couple(session, couple)
    await session.flush()  # the members' active rows are closed before they get new ones
    for member in members:
        user = await session.get(User, member.user_id)
        assert user is not None
        old_profile = (
            await session.execute(
                select(PartnerProfile).where(
                    PartnerProfile.couple_id == couple_id,
                    PartnerProfile.subject_user_id == member.user_id,
                )
            )
        ).scalar_one_or_none()
        await _new_solo_couple(session, user, old_profile)


async def purge_couple(session: AsyncSession, couple_id: uuid.UUID) -> None:
    """Delete an archived couple and, through the foreign keys, all of its data. A couple that is
    gone or active again (FR-PAIR-3) is left alone, so a second run no-ops."""
    status = (
        await session.execute(select(Couple.status).where(Couple.id == couple_id).with_for_update())
    ).scalar_one_or_none()
    if status != "archived":
        return
    await session.execute(delete(Couple).where(Couple.id == couple_id))
