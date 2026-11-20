"""Magic-link tokens, sign-up and sessions (FR-AUTH-1, FR-AUTH-3, FR-PAIR-0).

Only sha256 hashes of tokens are stored. Nothing here logs a token, a link or an email
address. Functions flush but leave the commit to the caller, except `verify_magic_link` and
the revoke functions, which own their transaction.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ApiError
from app.models import (
    Couple,
    CoupleConstraints,
    CoupleMember,
    MagicLinkToken,
    PartnerProfile,
    User,
    UserSession,
)
from app.schemas.auth import MeOut, is_valid_timezone
from app.security.tokens import hash_token, new_token

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session"
USER_AGENT_MAX = 200
LAST_SEEN_INTERVAL = timedelta(minutes=5)
MAGIC_LINK_WINDOW = timedelta(minutes=15)
MAGIC_LINK_PER_EMAIL = 5
# NO_LOGIN household: the husband owns the couple, the wife is its partner member.
HUSBAND_EMAIL = "husband@household.invalid"
WIFE_EMAIL = "wife@household.invalid"
# Addresses under this domain belong to the household and can never sign in by email.
HOUSEHOLD_DOMAIN = "household.invalid"


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_household_email(email: str) -> bool:
    return normalize_email(email).endswith("@" + HOUSEHOLD_DOMAIN)


def invalid_token_error() -> ApiError:
    return ApiError(400, "invalid_token", "This sign-in link is invalid or has expired.")


async def issue_magic_link(
    session: AsyncSession, settings: Settings, email: str, requester_ip: str | None = None
) -> str:
    """Store a fresh single-use token for `email` and return the link to send.

    The token is in the URL fragment, so it never reaches a server log or a Referer.
    The caller commits.
    """
    token = new_token()
    session.add(
        MagicLinkToken(
            email=normalize_email(email),
            token_hash=hash_token(token),
            expires_at=func.now() + timedelta(minutes=settings.magic_link_ttl_minutes),
            requester_ip=requester_ip,
        )
    )
    await session.flush()
    return f"{settings.origin}/auth/verify#token={token}"


async def lock_email(session: AsyncSession, email: str) -> None:
    """Serialise magic-link requests for one address until this transaction ends.

    Without it two concurrent requests could both count 4 tokens, both pass the limit and
    both insert. The lock is released at commit or rollback, so the count and the insert
    must stay in the transaction that took it.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:email, 0))"),
        {"email": normalize_email(email)},
    )


async def magic_link_retry_after(session: AsyncSession, email: str) -> int | None:
    """None if `email` may get another link; else the seconds until it may.

    Counts stored tokens, so the limit survives a restart.
    """
    count, oldest, now = (
        await session.execute(
            select(func.count(), func.min(MagicLinkToken.created_at), func.now()).where(
                MagicLinkToken.email == normalize_email(email),
                MagicLinkToken.created_at > func.now() - MAGIC_LINK_WINDOW,
            )
        )
    ).one()
    if count < MAGIC_LINK_PER_EMAIL:
        return None
    return max(1, int((oldest + MAGIC_LINK_WINDOW - now).total_seconds()) + 1)


async def membership(
    session: AsyncSession, user_id: uuid.UUID
) -> tuple[uuid.UUID | None, str | None]:
    """(couple_id, role) of the user's active couple, or (None, None)."""
    row = (
        await session.execute(
            select(CoupleMember.couple_id, CoupleMember.role).where(
                CoupleMember.user_id == user_id, CoupleMember.left_at.is_(None)
            )
        )
    ).first()
    return (row[0], row[1]) if row else (None, None)


def me_out(user: User, couple_id: uuid.UUID | None, role: str | None) -> MeOut:
    return MeOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        timezone=user.timezone,
        couple_id=couple_id,
        role=role,
    )


async def _create_account(
    session: AsyncSession, email: str, timezone: str, display_name: str | None = None
) -> User:
    """FR-PAIR-0: the user and their couple space, all at once."""
    user = User(email=email, timezone=timezone, display_name=display_name)
    couple = Couple(timezone=timezone, status="active")
    session.add_all([user, couple])
    await session.flush()
    session.add_all(
        [
            CoupleMember(couple_id=couple.id, user_id=user.id, role="owner"),
            PartnerProfile(couple_id=couple.id, slot="owner", subject_user_id=user.id),
            PartnerProfile(couple_id=couple.id, slot="partner", subject_user_id=None),
            CoupleConstraints(couple_id=couple.id),
        ]
    )
    await session.flush()
    return user


async def _find_user(session: AsyncSession, email: str) -> User | None:
    return (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()


@dataclass(frozen=True)
class SignedIn:
    user: User
    couple_id: uuid.UUID | None
    role: str | None
    session_token: str


async def _open_session(
    session: AsyncSession, settings: Settings, user: User, user_agent: str | None
) -> SignedIn:
    """Add a session row for `user` and commit the transaction."""
    couple_id, role = await membership(session, user.id)
    raw = new_token()
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=func.now() + timedelta(days=settings.session_ttl_days),
            user_agent=user_agent[:USER_AGENT_MAX] if user_agent else None,
        )
    )
    await session.commit()
    return SignedIn(user=user, couple_id=couple_id, role=role, session_token=raw)


async def verify_magic_link(
    session: AsyncSession,
    settings: Settings,
    token: str,
    timezone: str | None,
    user_agent: str | None,
) -> SignedIn:
    """Redeem a magic-link token: claim it, find or create the account, open a session.

    One transaction. The claim is a single UPDATE ... RETURNING, so of two concurrent
    verifies of the same token exactly one gets a row. Any failure is the same
    `400 invalid_token`.
    """
    email = (
        await session.execute(
            update(MagicLinkToken)
            .where(
                MagicLinkToken.token_hash == hash_token(token),
                MagicLinkToken.used_at.is_(None),
                MagicLinkToken.expires_at > func.now(),
            )
            .values(used_at=func.now())
            .returning(MagicLinkToken.email)
        )
    ).scalar_one_or_none()
    if email is None or is_household_email(email):
        await session.rollback()
        raise invalid_token_error()

    user = await _find_user(session, email)
    if user is None:
        zone = timezone if timezone is not None and is_valid_timezone(timezone) else "UTC"
        try:
            async with session.begin_nested():
                user = await _create_account(session, email, zone)
        except IntegrityError:
            # Two different links for the same new email were redeemed at once; the other
            # transaction won the insert.
            user = await _find_user(session, email)
    if user is None or user.deleted_at is not None:
        await session.rollback()
        raise invalid_token_error()

    return await _open_session(session, settings, user, user_agent)


async def revoke_session(session: AsyncSession, session_id: uuid.UUID) -> None:
    await session.execute(
        update(UserSession)
        .where(UserSession.id == session_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    await session.commit()


async def revoke_all_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    await session.commit()


async def _get_or_create(
    session: AsyncSession, email: str, create: Callable[[], Awaitable[User]]
) -> User:
    """Find the user, or create them in a savepoint. A concurrent creator wins the unique
    index; this one then re-fetches the winner's row."""
    user = await _find_user(session, email)
    if user is None:
        try:
            async with session.begin_nested():
                user = await create()
        except IntegrityError:
            user = await _find_user(session, email)
    if user is None:
        raise HouseholdConflict("profile_not_creatable")
    if user.deleted_at is not None:
        raise HouseholdConflict("profile_deleted")
    return user


class HouseholdConflict(Exception):
    """The NO_LOGIN household is not in the shape choose_profile relies on. The message is a
    fixed reason code, never an address."""


async def _active_membership(
    session: AsyncSession, user_id: uuid.UUID
) -> tuple[uuid.UUID, str, str] | None:
    """(couple_id, role, couple status) of the user's active membership."""
    row = (
        await session.execute(
            select(CoupleMember.couple_id, CoupleMember.role, Couple.status)
            .join(Couple, Couple.id == CoupleMember.couple_id)
            .where(CoupleMember.user_id == user_id, CoupleMember.left_at.is_(None))
        )
    ).first()
    return (row[0], row[1], row[2]) if row else None


async def _check_household(session: AsyncSession, husband: User, wife: User | None) -> uuid.UUID:
    """Check the whole household, for every choice. Returns the couple id.

    The husband is the active owner of an active couple and the subject of its owner slot;
    the couple has its constraints row; its active members are exactly the husband plus,
    optionally, the household wife as partner; and the partner slot exists and is unclaimed
    or the wife's.
    """
    member = await _active_membership(session, husband.id)
    if member is None or member[1] != "owner" or member[2] != "active":
        raise HouseholdConflict("husband_not_owner")
    couple_id = member[0]
    if wife is not None and wife.deleted_at is not None:
        raise HouseholdConflict("wife_deleted")

    subjects = {
        slot: subject
        for slot, subject in (
            await session.execute(
                select(PartnerProfile.slot, PartnerProfile.subject_user_id).where(
                    PartnerProfile.couple_id == couple_id
                )
            )
        ).all()
    }
    if subjects.get("owner") != husband.id:
        raise HouseholdConflict("owner_slot_not_husband")
    if "partner" not in subjects:
        raise HouseholdConflict("partner_slot_missing")
    if subjects["partner"] is not None and (wife is None or subjects["partner"] != wife.id):
        raise HouseholdConflict("partner_slot_unavailable")

    constraints = (
        await session.execute(
            select(CoupleConstraints.couple_id).where(CoupleConstraints.couple_id == couple_id)
        )
    ).first()
    if constraints is None:
        raise HouseholdConflict("constraints_missing")

    members = (
        await session.execute(
            select(CoupleMember.user_id, CoupleMember.role).where(
                CoupleMember.couple_id == couple_id, CoupleMember.left_at.is_(None)
            )
        )
    ).all()
    others = [(user_id, role) for user_id, role in members if user_id != husband.id]
    if len(others) > 1 or any(wife is None or u != wife.id or r != "partner" for u, r in others):
        raise HouseholdConflict("unexpected_members")
    # A claimed partner slot belongs to the wife, and she must be this couple's partner member.
    if subjects["partner"] is not None and not others:
        raise HouseholdConflict("slot_holder_not_a_member")
    return couple_id


async def _check_and_claim_wife(session: AsyncSession, wife: User, couple_id: uuid.UUID) -> None:
    """The wife must be the active partner member of this couple and hold its partner slot
    (claiming it if it is unclaimed)."""
    member = await _active_membership(session, wife.id)
    if member is None or member[1] != "partner" or member[0] != couple_id:
        raise HouseholdConflict("wife_not_partner_of_household")
    claimed = (
        await session.execute(
            update(PartnerProfile)
            .where(
                PartnerProfile.couple_id == couple_id,
                PartnerProfile.slot == "partner",
                PartnerProfile.subject_user_id.is_(None),
            )
            .values(subject_user_id=wife.id)
            .returning(PartnerProfile.id)
        )
    ).first()
    if claimed is not None:
        return
    subject = (
        await session.execute(
            select(PartnerProfile.subject_user_id).where(
                PartnerProfile.couple_id == couple_id, PartnerProfile.slot == "partner"
            )
        )
    ).first()
    if subject is None or subject[0] != wife.id:
        raise HouseholdConflict("partner_slot_unavailable")


async def choose_profile(
    session: AsyncSession,
    settings: Settings,
    profile: str,
    user_agent: str | None,
    replace_session_id: uuid.UUID | None,
) -> SignedIn:
    """NO_LOGIN mode: act as the household's husband (owner) or wife (partner).

    Creates the household on first use (the husband's couple space, as at sign-up) and the
    wife as the couple's partner member, claiming the partner profile slot. One transaction;
    existing household rows are checked, and any mismatch rolls everything back and raises
    HouseholdConflict.
    """

    async def create_husband() -> User:
        return await _create_account(session, HUSBAND_EMAIL, "UTC", "Husband")

    try:
        user = await _get_or_create(session, HUSBAND_EMAIL, create_husband)
        husband = user
        existing_wife = await _find_user(session, WIFE_EMAIL)
        couple_id = await _check_household(session, husband, existing_wife)
        if profile == "wife":

            async def create_wife() -> User:
                wife = User(email=WIFE_EMAIL, timezone=husband.timezone, display_name="Wife")
                session.add(wife)
                await session.flush()
                session.add(CoupleMember(couple_id=couple_id, user_id=wife.id, role="partner"))
                await session.flush()
                return wife

            user = await _get_or_create(session, WIFE_EMAIL, create_wife)
            await _check_household(session, husband, user)
            await _check_and_claim_wife(session, user, couple_id)
    except HouseholdConflict as exc:
        await session.rollback()
        logger.error("no-login household is in an unexpected state: %s", exc)
        raise
    if replace_session_id is not None:
        await session.execute(
            update(UserSession)
            .where(UserSession.id == replace_session_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )
    return await _open_session(session, settings, user, user_agent)
