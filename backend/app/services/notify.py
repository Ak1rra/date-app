"""Notification creation and quiet-hours delivery scheduling."""

from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Notification, User
from app.schemas.notify import KINDS, NotificationSettings
from app.worker.jobs import enqueue

DEFAULT_SETTINGS = {"kinds": {}, "quiet_start": "22:00", "quiet_end": "08:00"}


def _parse_time(value: str, fallback: time) -> time:
    try:
        hour, minute = value.split(":")
        parsed = time(int(hour), int(minute))
    except (AttributeError, TypeError, ValueError):
        return fallback
    return parsed


def effective_settings(raw: dict[str, Any]) -> NotificationSettings:
    """Return the supported settings, filling defaults and dropping unknown values."""
    kinds: dict[str, bool] = {kind: True for kind in KINDS}
    raw_kinds = raw.get("kinds") if isinstance(raw, dict) else None
    if isinstance(raw_kinds, dict):
        for kind in KINDS:
            value = raw_kinds.get(kind)
            if isinstance(value, bool):
                kinds[kind] = value

    start = _parse_time(str(raw.get("quiet_start", "22:00")), time(22, 0))
    end = _parse_time(str(raw.get("quiet_end", "08:00")), time(8, 0))
    return NotificationSettings(
        kinds=kinds,
        quiet_start=f"{start.hour:02d}:{start.minute:02d}",
        quiet_end=f"{end.hour:02d}:{end.minute:02d}",
    )


def _aware_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def deliver_after_for(now: datetime, tz: str, quiet_start: time, quiet_end: time) -> datetime:
    """Move a timestamp in a local quiet window to that window's next local end."""
    moment = _aware_utc(now)
    if quiet_start == quiet_end:
        return now
    try:
        zone = ZoneInfo(tz)
    except (KeyError, TypeError):
        zone = ZoneInfo("UTC")
    local = moment.astimezone(zone)
    local_time = local.timetz().replace(tzinfo=None)
    wraps = quiet_start > quiet_end
    inside = (
        local_time >= quiet_start or local_time < quiet_end
        if wraps
        else quiet_start <= local_time < quiet_end
    )
    if not inside:
        return now

    end_date = local.date()
    if wraps and local_time >= quiet_start:
        end_date += timedelta(days=1)
    target = datetime.combine(end_date, quiet_end, tzinfo=zone)
    # This also protects the non-wrapping case at an unusual DST transition where a local
    # clock time is repeated or skipped.
    if target.astimezone(UTC) <= moment:
        # At a fall-back transition the end time can occur twice; the second one may be ahead.
        repeat = target.replace(fold=1)
        if repeat.astimezone(UTC) > moment:
            return repeat.astimezone(UTC)
        target = datetime.combine(end_date + timedelta(days=1), quiet_end, tzinfo=zone)
    return target.astimezone(UTC)


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def notify(
    session: AsyncSession,
    user_id: UUID,
    kind: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    *,
    couple_id: UUID | None = None,
    dedupe_key: str | None = None,
    respect_quiet_hours: bool = True,
    settings: Settings,
) -> Notification | None:
    """Create an in-app notification and, when eligible, its push job.

    The caller owns the transaction and commits it after this function returns.
    """
    user = await session.get(User, user_id)
    if user is None:
        return None
    now = _utcnow()
    user_settings = effective_settings(user.notification_settings or {})
    start = _parse_time(user_settings.quiet_start, time(22, 0))
    end = _parse_time(user_settings.quiet_end, time(8, 0))
    deliver_after = (
        deliver_after_for(now, user.timezone, start, end) if respect_quiet_hours else now
    )

    stmt = (
        pg_insert(Notification)
        .values(
            user_id=user.id,
            couple_id=couple_id,
            kind=kind,
            title=title,
            body=body,
            data=data or {},
            deliver_after=deliver_after,
            dedupe_key=dedupe_key,
        )
        .on_conflict_do_nothing(index_elements=[Notification.dedupe_key])
        .returning(Notification.id)
    )
    notification_id = (await session.execute(stmt)).scalar_one_or_none()
    if notification_id is None:
        return None
    notification = await session.get(Notification, notification_id)
    if notification is None:  # pragma: no cover - the just-inserted row cannot disappear here
        return None

    push_enabled = (
        kind in KINDS
        and user_settings.kinds.get(kind, True)
        and bool(settings.vapid_public_key and settings.vapid_private_key)
    )
    if push_enabled:
        await enqueue(
            session,
            "push.send",
            {"notification_id": str(notification.id)},
            run_at=deliver_after,
            dedupe_key=f"push.send:{notification.id}",
        )
    return notification
