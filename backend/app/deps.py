"""Shared FastAPI dependencies."""

import uuid
from dataclasses import dataclass
from typing import cast

from fastapi import Depends, Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import get_session
from app.errors import ApiError
from app.models import User, UserSession
from app.security.ratelimit import SlidingWindowLimiter
from app.security.tokens import hash_token
from app.services.auth import LAST_SEEN_INTERVAL, SESSION_COOKIE_NAME, membership


def get_app_settings(request: Request) -> Settings:
    """The Settings the app was built with (tests build apps with their own)."""
    return cast(Settings, request.app.state.settings)


def require_email_login(settings: Settings = Depends(get_app_settings)) -> None:
    """Email sign-in routes do not exist in NO_LOGIN mode. Listed in a route's
    `dependencies`, so it runs before the route's other dependencies (email provider,
    limiters) and before body validation."""
    if settings.no_login:
        raise ApiError(404, "not_found", "Not found.")


def require_no_login(settings: Settings = Depends(get_app_settings)) -> None:
    """The profile picker only exists in NO_LOGIN mode. Listed in a route's `dependencies`
    so it runs before body validation and the route's other dependencies."""
    if not settings.no_login:
        raise ApiError(404, "not_found", "Not found.")


def get_magic_link_ip_limiter(request: Request) -> SlidingWindowLimiter:
    return cast(SlidingWindowLimiter, request.app.state.magic_link_ip_limiter)


def get_verify_ip_limiter(request: Request) -> SlidingWindowLimiter:
    return cast(SlidingWindowLimiter, request.app.state.verify_ip_limiter)


def client_ip(request: Request) -> str:
    """The caller's address (uvicorn already applied any trusted X-Forwarded-For)."""
    return request.client.host if request.client else "unknown"


@dataclass(frozen=True)
class CurrentUser:
    """The signed-in user, their session and their active couple (None until they have one)."""

    user: User
    session_id: uuid.UUID
    couple_id: uuid.UUID | None
    role: str | None


async def user_from_cookie(session: AsyncSession, raw: str | None) -> CurrentUser | None:
    """Resolve a raw `session` cookie value to a user, or None if it is missing or no longer
    valid. Shared by the HTTP dependency and the WebSocket handshake."""
    if not raw:
        return None
    row = (
        await session.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(
                UserSession.token_hash == hash_token(raw),
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > func.now(),
                User.deleted_at.is_(None),
            )
        )
    ).first()
    if row is None:
        return None
    user_session, user = row
    # Most requests only read; touch the row at most once per LAST_SEEN_INTERVAL.
    touched = await session.execute(
        update(UserSession)
        .where(
            UserSession.id == user_session.id,
            UserSession.last_seen_at < func.now() - LAST_SEEN_INTERVAL,
        )
        .values(last_seen_at=func.now())
    )
    if touched.rowcount:  # type: ignore[attr-defined]
        await session.commit()
    couple_id, role = await membership(session, user.id)
    return CurrentUser(user=user, session_id=user_session.id, couple_id=couple_id, role=role)


async def optional_current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> CurrentUser | None:
    """Resolve the `session` cookie to a user, or None if it is missing or no longer valid."""
    return await user_from_cookie(session, request.cookies.get(SESSION_COOKIE_NAME))


async def current_user(
    found: CurrentUser | None = Depends(optional_current_user),
) -> CurrentUser:
    if found is None:
        raise ApiError(401, "unauthenticated", "Please sign in to continue.")
    return found
