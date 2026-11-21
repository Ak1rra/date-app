"""Job handlers. Importing this module registers them (app.worker.runner does).

Handlers are idempotent and leave committing to the job runner (see app.worker.jobs).
"""

import asyncio
import json
import logging
import unicodedata
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from pywebpush import WebPushException, webpush
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.ai import ProviderCache
from app.models.ideas import Idea
from app.models.itinerary import Itinerary
from app.models.jobs import Job
from app.models.notify import Notification, PushSubscription
from app.models.user import MagicLinkToken, UserSession
from app.services.surprise import SURPRISE_DUE, process_due
from app.worker.jobs import enqueue, register

logger = logging.getLogger("app.worker")

AUTH_CLEANUP = "auth.cleanup"
CACHE_CLEANUP = "cache.cleanup"

MAGIC_LINK_RETENTION = timedelta(days=1)  # after expires_at
SESSION_RETENTION = timedelta(days=7)  # after expires_at or revoked_at
JOB_RETENTION = timedelta(days=7)  # finished (done or failed) jobs, after finished_at


def _push_url(data: dict[str, Any]) -> str:
    value = data.get("url")
    if (
        isinstance(value, str)
        and len(value) <= 300
        and value.startswith("/")
        and not value.startswith("//")
        and not urlsplit(value).scheme
        and "\\" not in value  # browsers can read /\evil as //evil
        # control (incl. C1) and format (bidi, zero-width) characters
        and not any(unicodedata.category(ch) in ("Cc", "Cf") for ch in value)
    ):
        return value
    return "/"


def _utcnow() -> datetime:
    return datetime.now(UTC)  # a seam, so tests can pin the hour


def _hour_start(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _cleanup_key(hour: datetime) -> str:
    return f"{AUTH_CLEANUP}:{hour.strftime('%Y-%m-%dT%H')}"


IDEAS_EXPIRE = "ideas.expire"


def _expire_key(day: date) -> str:
    return f"{IDEAS_EXPIRE}:{day.isoformat()}"


async def schedule_ideas_expire(session: AsyncSession, *, next_day: bool) -> bool:
    """Enqueue the ideas.expire job for today (due now) or tomorrow (due at 00:00 UTC). One job
    per day: the dedupe key is the date, so repeats are no-ops. The caller commits."""
    day = _utcnow().astimezone(UTC).date()
    if next_day:
        day += timedelta(days=1)
        run_at = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
        return await enqueue(session, IDEAS_EXPIRE, {}, run_at=run_at, dedupe_key=_expire_key(day))
    return await enqueue(session, IDEAS_EXPIRE, {}, dedupe_key=_expire_key(day))


@register(IDEAS_EXPIRE)
async def ideas_expire(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Mark active grounded ideas whose `valid_until` has passed as expired (FR-IDEA-4), then
    schedule the next run. Self-scheduling, so a missed day just runs late."""
    await session.execute(
        update(Idea)
        .where(Idea.grounded.is_(True), Idea.status == "active", Idea.valid_until < func.now())
        .values(status="expired", updated_at=func.now())
    )
    await schedule_ideas_expire(session, next_day=True)


async def schedule_auth_cleanup(session: AsyncSession, *, next_hour: bool) -> bool:
    """Enqueue the auth.cleanup job for the current hour (due now) or the next one (due at its
    start). One job per hour: the dedupe key is the hour, so repeats are no-ops.
    The caller commits."""
    hour = _hour_start(_utcnow())
    if next_hour:
        hour += timedelta(hours=1)
        return await enqueue(session, AUTH_CLEANUP, {}, run_at=hour, dedupe_key=_cleanup_key(hour))
    return await enqueue(session, AUTH_CLEANUP, {}, dedupe_key=_cleanup_key(hour))


@register(AUTH_CLEANUP)
async def auth_cleanup(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Delete magic-link tokens expired over a day ago, sessions expired or revoked over a week
    ago and jobs finished over a week ago, then schedule the next run. Self-scheduling, so a
    missed hour just runs late."""
    now = func.now()
    await session.execute(
        delete(MagicLinkToken).where(MagicLinkToken.expires_at < now - MAGIC_LINK_RETENTION)
    )
    await session.execute(
        delete(UserSession).where(
            or_(
                UserSession.expires_at < now - SESSION_RETENTION,
                UserSession.revoked_at < now - SESSION_RETENTION,
            )
        )
    )
    await session.execute(
        delete(Job).where(Job.status.in_(("done", "failed")), Job.finished_at < now - JOB_RETENTION)
    )
    await schedule_auth_cleanup(session, next_hour=True)


def _cache_cleanup_key(hour: datetime) -> str:
    return f"{CACHE_CLEANUP}:{hour.strftime('%Y-%m-%dT%H')}"


async def schedule_cache_cleanup(session: AsyncSession, *, next_hour: bool) -> bool:
    """Enqueue the cache.cleanup job for the current hour (due now) or the next one (due at its
    start). One job per hour: the dedupe key is the hour, so repeats are no-ops.
    The caller commits."""
    hour = _hour_start(_utcnow())
    if next_hour:
        hour += timedelta(hours=1)
        return await enqueue(
            session, CACHE_CLEANUP, {}, run_at=hour, dedupe_key=_cache_cleanup_key(hour)
        )
    return await enqueue(session, CACHE_CLEANUP, {}, dedupe_key=_cache_cleanup_key(hour))


@register(CACHE_CLEANUP)
async def cache_cleanup(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Delete provider_cache rows past their expires_at, then schedule the next run.
    Self-scheduling, so a missed hour just runs late."""
    await session.execute(delete(ProviderCache).where(ProviderCache.expires_at < func.now()))
    await schedule_cache_cleanup(session, next_hour=True)


@register(SURPRISE_DUE)
async def surprise_due(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Release the hints and reveal the surprise whose time has come. Stale jobs are harmless:
    `process_due` re-checks the itinerary's state."""
    try:
        itinerary_id = uuid.UUID(str(payload.get("itinerary_id")))
    except (AttributeError, ValueError):
        return
    itinerary = await session.get(Itinerary, itinerary_id)
    if itinerary is None:
        return
    await process_due(session, itinerary, get_settings(), _utcnow())


@register("push.send")
async def push_send(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Deliver one notification to all of its current subscriptions."""
    try:
        notification_id = uuid.UUID(str(payload.get("notification_id")))
    except (AttributeError, ValueError):
        return
    notification = await session.get(Notification, notification_id)
    settings = get_settings()
    if (
        notification is None
        or notification.pushed_at is not None
        or notification.read_at is not None
        or not settings.vapid_private_key
        or not settings.vapid_public_key
    ):
        return

    subscriptions = (
        (
            await session.execute(
                select(PushSubscription).where(PushSubscription.user_id == notification.user_id)
            )
        )
        .scalars()
        .all()
    )
    data = notification.data if isinstance(notification.data, dict) else {}
    push_data = json.dumps(
        {"title": notification.title, "body": notification.body, "url": _push_url(data)}
    )
    delivered = False
    retryable_failure = False
    for subscription in subscriptions:
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                },
                data=push_data,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject or "mailto:admin@dates.lan"},
                ttl=86400,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                await session.delete(subscription)
            else:
                retryable_failure = True
                logger.warning("push delivery failed: %s", type(exc).__name__)
        except Exception as exc:  # pragma: no cover - vendor/client errors vary by backend
            retryable_failure = True
            logger.warning("push delivery failed: %s", type(exc).__name__)
        else:
            delivered = True
            subscription.last_used_at = _utcnow()
    if retryable_failure and not delivered:
        raise RuntimeError("all push deliveries failed")
    notification.pushed_at = _utcnow()
