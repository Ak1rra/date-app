"""Magic-link tokens, sign-up and sessions (FR-AUTH-1, FR-AUTH-3, FR-PAIR-0).

Only sha256 hashes of tokens are stored. Nothing here logs a token, a link or an email
address. Functions flush but leave the commit to the caller, except `verify_magic_link` and
the revoke functions, which own their transaction.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
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

SESSION_COOKIE_NAME = "session"
USER_AGENT_MAX = 200
LAST_SEEN_INTERVAL = timedelta(minutes=5)
MAGIC_LINK_WINDOW = timedelta(minutes=15)
MAGIC_LINK_PER_EMAIL = 5


def normalize_email(email: str) -> str:
    return email.strip().lower()


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


async def _create_account(session: AsyncSession, email: str, timezone: str) -> User:
    """FR-PAIR-0: the user and their couple space, all at once."""
    user = User(email=email, timezone=timezone)
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
    if email is None:
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
