"""Reminders (FR-NOTIF): the day-before and hour-before nudges for a scheduled date and the
checklist-due nudge for the planner.

Each reminder is a job enqueued when the date is scheduled (or a checklist item gets a due
time). The jobs carry the time they were made for, and their handlers do nothing if the date or
item has moved since, so rescheduling leaves harmless stale jobs behind.

A surprise the surprisee hasn't had revealed gets generic text that never names the title, the
venue or a stop (FR-NOTIF-5).

Functions flush but leave the commit to the caller. This module must not import
`app.services.itineraries` at module level: that module imports this one.
"""

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import ChecklistItem, Couple, CoupleMember, Itinerary, Notification, User
from app.providers.registry import Providers
from app.providers.weather.base import LOW_CODES, MEANINGFUL_CODES, condition_label
from app.services.notify import deliver_after_for, effective_settings, notify
from app.worker.jobs import enqueue

REMINDER_DAY_BEFORE = "reminder.day_before"
REMINDER_HOUR_BEFORE = "reminder.hour_before"
CHECKLIST_DUE = "checklist.due"

DAY_BEFORE_HOUR = dt.time(18, 0)
CHECKLIST_LEAD = dt.timedelta(hours=24)
SHOWN_MAX = 3  # bring items and warnings listed in a reminder
# A day-before reminder this close to the start (a delayed job) would read wrong: skip it.
DAY_BEFORE_CUTOFF = dt.timedelta(hours=2)

Which = Literal["day", "hour"]


def _utc(moment: dt.datetime) -> dt.datetime:
    return moment.astimezone(dt.UTC)


def clock_label(local: dt.datetime) -> str:
    hour = local.strftime("%I").lstrip("0") or "0"
    return f"{hour}:{local:%M} {local:%p}"


def local_label(local: dt.datetime) -> str:
    return f"{local:%a} {local.day} {local:%b}, {clock_label(local)}"


def _zone(name: str) -> ZoneInfo:
    from app.services.itineraries import zone_for  # deferred: itineraries imports this module

    return zone_for(name)


def _parse(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


async def schedule_reminders(session: AsyncSession, itinerary: Itinerary) -> None:
    """Enqueue the day-before (18:00 couple-local the day before) and hour-before reminders for
    a scheduled date, each only if still in the future. The caller commits."""
    if itinerary.status != "scheduled" or itinerary.starts_at is None:
        return
    couple = await session.get(Couple, itinerary.couple_id)
    if couple is None:
        return
    starts_at = _utc(itinerary.starts_at)
    starts_iso = starts_at.isoformat()
    zone = _zone(couple.timezone)
    day_before = starts_at.astimezone(zone).date() - dt.timedelta(days=1)
    moment = dt.datetime.now(dt.UTC)
    payload = {"itinerary_id": str(itinerary.id), "starts_at": starts_iso}
    when_day = _utc(dt.datetime.combine(day_before, DAY_BEFORE_HOUR, tzinfo=zone))
    if when_day > moment:
        await enqueue(
            session,
            REMINDER_DAY_BEFORE,
            payload,
            run_at=when_day,
            dedupe_key=f"remind-day:{itinerary.id}:{starts_iso}",
        )
    when_hour = starts_at - dt.timedelta(hours=1)
    if when_hour > moment:
        await enqueue(
            session,
            REMINDER_HOUR_BEFORE,
            payload,
            run_at=when_hour,
            dedupe_key=f"remind-hour:{itinerary.id}:{starts_iso}",
        )
    # Items added before the date was scheduled (the AI planner inserts them with due times)
    # get their nudge now; the same dedupe key as the API paths, so nothing doubles.
    items = (
        (
            await session.execute(
                select(ChecklistItem).where(
                    ChecklistItem.itinerary_id == itinerary.id,
                    ChecklistItem.due_at.is_not(None),
                    ChecklistItem.done_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for item in items:
        await schedule_checklist_due(session, itinerary, item)


async def _members(session: AsyncSession, couple_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await session.execute(
                select(CoupleMember.user_id).where(
                    CoupleMember.couple_id == couple_id, CoupleMember.left_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )


async def _bring(session: AsyncSession, itinerary_id: uuid.UUID) -> list[str]:
    """The texts of every unticked item, in checklist order."""
    return list(
        (
            await session.execute(
                select(ChecklistItem.text)
                .where(ChecklistItem.itinerary_id == itinerary_id, ChecklistItem.done_at.is_(None))
                .order_by(ChecklistItem.position)
            )
        )
        .scalars()
        .all()
    )


def _capped(texts: list[str], sep: str) -> str:
    """Up to three joined by `sep`, then the sentence's full stop and " (+N more)"."""
    more = f" (+{len(texts) - SHOWN_MAX} more)" if len(texts) > SHOWN_MAX else ""
    return f"{sep.join(texts[:SHOWN_MAX])}.{more}"


def _forecast_line(weather: dict[str, Any] | None) -> str | None:
    """The line "Forecast: 64°F, light rain." from the stored forecast (hourly temp_c and
    weather_code), or None when there are no hours: the warmest hour, in °F, and the worst
    condition."""
    hourly = [h for h in (weather or {}).get("hourly") or [] if isinstance(h, dict)]
    temps = [float(h["temp_c"]) for h in hourly if isinstance(h.get("temp_c"), int | float)]
    codes = [h["weather_code"] for h in hourly if isinstance(h.get("weather_code"), int)]
    if not temps:
        return None
    line = f"{round(max(temps) * 9 / 5 + 32)}°F"
    if codes:
        worst = max(codes, key=lambda c: (c in MEANINGFUL_CODES, c in LOW_CODES, c))
        line += f", {(condition_label(worst) or 'mixed conditions').lower()}"
    return f"Forecast: {line}."


def _starts_in_quiet_hours(user: User, starts_at: dt.datetime) -> bool:
    quiet = effective_settings(user.notification_settings or {})
    start = dt.time.fromisoformat(quiet.quiet_start)
    end = dt.time.fromisoformat(quiet.quiet_end)
    return deliver_after_for(starts_at, user.timezone, start, end) != starts_at


async def send_reminder(
    session: AsyncSession,
    which: Which,
    payload: dict[str, Any],
    settings: Settings,
    providers_for: Callable[[uuid.UUID], Providers],
) -> None:
    """Send one reminder to each active member, unless the date has moved or been cancelled
    since the job was made. The day-before reminder re-validates the date first, so its weather
    and warnings are fresh. `providers_for(couple_id)` builds the providers that needs."""
    try:
        itinerary_id = uuid.UUID(str(payload.get("itinerary_id")))
    except ValueError:
        return
    expected = _parse(payload.get("starts_at"))
    # Lock the date first and do every check and insert under the lock, so a concurrent
    # schedule, cancel or reveal waits for this job (or this job sees its result).
    itinerary = (
        await session.execute(
            select(Itinerary)
            .where(Itinerary.id == itinerary_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if (
        itinerary is None
        or expected is None
        or itinerary.status != "scheduled"
        or itinerary.starts_at is None
        or itinerary.starts_at != expected
    ):
        return
    couple = await session.get(Couple, itinerary.couple_id)
    if couple is None:
        return
    starts_at = _utc(itinerary.starts_at)
    # A worker outage can delay a job past the moment its wording makes sense.
    late = dt.datetime.now(dt.UTC) + (DAY_BEFORE_CUTOFF if which == "day" else dt.timedelta())
    if starts_at <= late:
        return
    starts_iso = starts_at.isoformat()
    local = starts_at.astimezone(_zone(couple.timezone))
    members = await _members(session, itinerary.couple_id)
    hidden = itinerary.is_surprise and itinerary.revealed_at is None
    surprisee = (
        {m for m in members if m != itinerary.planner_user_id} if hidden else set[uuid.UUID]()
    )

    extra = ""
    if which == "day" and set(members) - surprisee:
        from app.services.itineraries import revalidate  # deferred: itineraries imports this

        await revalidate(session, itinerary, providers_for(itinerary.couple_id), settings)
        bring = await _bring(session, itinerary.id)
        if bring:
            extra += f" Bring: {_capped(bring, ', ')}"
        if itinerary.bring_wear:
            extra += f" Wear: {_capped([itinerary.bring_wear.strip().rstrip('.')], ', ')}"
        forecast = _forecast_line(itinerary.weather)
        if forecast:
            extra += f" {forecast}"
        messages = [
            str(w["message"])
            for w in itinerary.warnings or []
            if isinstance(w, dict) and w.get("message")
        ]
        if messages:
            extra += f" Heads-up: {_capped(messages, '; ')}"

    kind = "reminder_day_before" if which == "day" else "reminder_hour_before"
    for user_id in members:
        user = await session.get(User, user_id)
        if user is None:
            continue
        if user_id in surprisee:
            if which == "day":
                title, body = (
                    "Your surprise date is tomorrow ✨",
                    "Get ready — hints are on the way.",
                )
            else:
                title, body = "Your surprise date is in 1 hour! ✨", "Time to get ready."
            url = f"/surprise/{itinerary.id}"
        else:
            if which == "day":
                title = "Your date is tomorrow 📅"
                body = f"{itinerary.title} tomorrow at {clock_label(local)}.{extra}"
            else:
                title = "Your date is in 1 hour ⏰"
                body = f"{itinerary.title} starts at {clock_label(local)}."
            url = f"/itineraries/{itinerary.id}"
        # An hour-before nudge for a date that itself starts in quiet hours is the exception:
        # waiting for the window to end would make it late.
        respect_quiet = which == "day" or not _starts_in_quiet_hours(user, starts_at)
        await notify(
            session,
            user_id,
            kind,
            title,
            body,
            # itinerary_id and starts_at let a quiet-hours deferred push check it is still current.
            {"url": url, "itinerary_id": str(itinerary.id), "starts_at": starts_iso},
            couple_id=itinerary.couple_id,
            dedupe_key=f"{kind}:{itinerary.id}:{user_id}:{starts_iso}",
            respect_quiet_hours=respect_quiet,
            settings=settings,
        )


async def schedule_checklist_due(
    session: AsyncSession, itinerary: Itinerary, item: ChecklistItem
) -> None:
    """Enqueue the nudge 24 hours before an item's due time, if that is still ahead. The
    caller commits."""
    if item.due_at is None:
        return
    due_iso = _utc(item.due_at).isoformat()
    run_at = _utc(item.due_at) - CHECKLIST_LEAD
    if run_at <= dt.datetime.now(dt.UTC):
        return
    await enqueue(
        session,
        CHECKLIST_DUE,
        {"item_id": str(item.id), "due_at": due_iso},
        run_at=run_at,
        dedupe_key=f"checklist:{item.id}:{due_iso}",
    )


async def send_checklist_due(
    session: AsyncSession, payload: dict[str, Any], settings: Settings
) -> None:
    """Remind the planner of an item still unticked and still due when the job was made for."""
    try:
        item_id = uuid.UUID(str(payload.get("item_id")))
    except ValueError:
        return
    expected = _parse(payload.get("due_at"))
    # Lock the item, then its date, and check under both locks (a tick or a new due time
    # waits for this job, or this job sees it).
    item = (
        await session.execute(
            select(ChecklistItem)
            .where(ChecklistItem.id == item_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if item is None or expected is None or item.due_at is None or item.due_at != expected:
        return
    if item.done_at is not None:
        return
    itinerary = (
        await session.execute(
            select(Itinerary)
            .where(Itinerary.id == item.itinerary_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if itinerary is None or itinerary.status == "cancelled":
        return
    couple = await session.get(Couple, itinerary.couple_id)
    if couple is None:
        return
    local = _utc(item.due_at).astimezone(_zone(couple.timezone))
    await notify(
        session,
        itinerary.planner_user_id,
        "checklist_due",
        "Checklist reminder 📝",
        f"{item.text} — due {local_label(local)}",
        {
            "url": f"/itineraries/{itinerary.id}",
            "itinerary_id": str(itinerary.id),
            "item_id": str(item.id),
            "due_at": _utc(item.due_at).isoformat(),
        },
        couple_id=itinerary.couple_id,
        dedupe_key=f"checklist:{item.id}:{_utc(item.due_at).isoformat()}",
        settings=settings,
    )


async def is_stale(session: AsyncSession, notification: Notification) -> bool:
    """Whether a reminder notification no longer matches its date or item, so a push deferred
    out of quiet hours should be dropped rather than sent with old details. A reminder row
    without the ids it was made for (older rows) is never stale."""
    data = notification.data if isinstance(notification.data, dict) else {}
    expected: dt.datetime | None = None
    item_id: uuid.UUID | None = None
    due: dt.datetime | None = None
    try:
        itinerary_id = uuid.UUID(str(data["itinerary_id"]))
        if notification.kind in ("reminder_day_before", "reminder_hour_before"):
            expected = _parse(data["starts_at"])
        elif notification.kind == "checklist_due":
            item_id = uuid.UUID(str(data["item_id"]))
            due = _parse(data["due_at"])
        else:
            return False
    except (KeyError, ValueError):
        return False
    itinerary = await session.get(Itinerary, itinerary_id)
    if item_id is None:
        return (
            itinerary is None
            or itinerary.status != "scheduled"
            or itinerary.starts_at is None
            or itinerary.starts_at != expected
        )
    item = await session.get(ChecklistItem, item_id)
    return (
        itinerary is None
        or itinerary.status == "cancelled"
        or item is None
        or item.done_at is not None
        or item.due_at is None
        or item.due_at != due
    )
