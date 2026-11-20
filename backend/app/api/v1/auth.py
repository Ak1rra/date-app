import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import get_session
from app.deps import (
    CurrentUser,
    client_ip,
    current_user,
    get_app_settings,
    get_magic_link_ip_limiter,
    get_verify_ip_limiter,
    optional_current_user,
)
from app.errors import ApiError
from app.providers.email.base import EmailDeliveryError, EmailSender
from app.providers.registry import get_email_sender
from app.schemas.auth import MagicLinkIn, OkOut, SessionOut, VerifyIn
from app.security.csrf import rotate_csrf_cookie
from app.security.ratelimit import SlidingWindowLimiter
from app.services import auth as auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _rate_limited(retry_after: int) -> ApiError:
    return ApiError(
        429,
        "rate_limited",
        "Too many attempts. Please wait a bit and try again.",
        {"Retry-After": str(retry_after)},
    )


def _cookie_kwargs(settings: Settings) -> dict[str, Any]:
    return {"path": "/", "secure": settings.cookie_secure, "httponly": True, "samesite": "lax"}


@router.post("/magic-link", status_code=202, response_model=OkOut)
async def request_magic_link(
    body: MagicLinkIn,
    request: Request,
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_session),
    sender: EmailSender = Depends(get_email_sender),
    ip_limiter: SlidingWindowLimiter = Depends(get_magic_link_ip_limiter),
) -> OkOut:
    """Email a sign-in link. Always 202 for a valid address, so accounts can't be probed."""
    ip = client_ip(request)
    if (wait := ip_limiter.hit(ip)) is not None:
        raise _rate_limited(wait)
    email = auth_service.normalize_email(body.email)
    if (wait := await auth_service.magic_link_retry_after(session, email)) is not None:
        raise _rate_limited(wait)
    link = await auth_service.issue_magic_link(session, settings, email, requester_ip=ip)
    await session.commit()
    try:
        await sender.send(
            to=email,
            subject="Your Date Planner sign-in link",
            text=(
                f"Use this link to sign in to Date Planner:\n\n{link}\n\n"
                f"It works once and expires in {settings.magic_link_ttl_minutes} minutes. "
                "If you didn't ask for it, you can ignore this email."
            ),
            link=link,
        )
    except EmailDeliveryError:
        # No exc_info and no address: the message could carry the recipient. The token row
        # stays; the user can ask again.
        logger.error("magic-link email delivery failed")
    return OkOut(ok=True)


@router.post("/verify", response_model=SessionOut)
async def verify(
    body: VerifyIn,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_session),
    ip_limiter: SlidingWindowLimiter = Depends(get_verify_ip_limiter),
) -> SessionOut:
    """Redeem a magic-link token: sign in (creating the account and couple space if new)."""
    if (wait := ip_limiter.hit(client_ip(request))) is not None:
        raise _rate_limited(wait)
    signed_in = await auth_service.verify_magic_link(
        session,
        settings,
        body.token,
        body.timezone,
        request.headers.get("user-agent"),
    )
    response.set_cookie(
        auth_service.SESSION_COOKIE_NAME,
        signed_in.session_token,
        max_age=settings.session_ttl_days * 86400,
        **_cookie_kwargs(settings),
    )
    rotate_csrf_cookie(response, settings)
    return SessionOut(
        authenticated=True,
        user=auth_service.me_out(signed_in.user, signed_in.couple_id, signed_in.role),
    )


@router.get("/session", response_model=SessionOut)
async def session_state(
    current: CurrentUser | None = Depends(optional_current_user),
) -> SessionOut:
    """Who is signed in, if anyone. Never 401: the app calls this at startup."""
    if current is None:
        return SessionOut(authenticated=False, user=None)
    return SessionOut(
        authenticated=True, user=auth_service.me_out(current.user, current.couple_id, current.role)
    )


def _clear_session_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(auth_service.SESSION_COOKIE_NAME, **_cookie_kwargs(settings))
    rotate_csrf_cookie(response, settings)


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> None:
    await auth_service.revoke_session(session, current.session_id)
    _clear_session_cookies(response, settings)


@router.post("/logout-all", status_code=204)
async def logout_all(
    response: Response,
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> None:
    await auth_service.revoke_all_sessions(session, current.user.id)
    _clear_session_cookies(response, settings)
