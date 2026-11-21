"""The shared calendar: dates and occasions in a range, and deterministic time suggestions.

Everything is read in the couple's timezone: local wall times are built with zoneinfo and
converted to UTC, so a day with a DST change keeps its real length. Reads only, nothing here
commits.
"""

import calendar as pycalendar
import datetime as dt
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import (
    AvailabilityBlock,
    AvailabilityWindow,
    Couple,
    ImportantDate,
    Itinerary,
    PartnerProfile,
)
from app.schemas.calendar import CalendarEntry, SuggestTimesIn, SuggestTimesOut, TimeSlot
from app.services.couple import not_found
from app.services.ideas_access import get_visible_idea
from app.services.itineraries import zone_for
from app.services.itinerary_access import upcoming_surprises, visible_itineraries

SURPRISE_ENTRY_TITLE = "Surprise date ✨"
MAX_RANGE = dt.timedelta(days=120)
MAX_SLOTS = 3
DEFAULT_DATE_LENGTH = dt.timedelta(hours=3)
BUFFER = dt.timedelta(minutes=60)
STEP_SECONDS = 30 * 60
# A profile with no availability set at all counts as free for this part of every day.
DEFAULT_WINDOW = (dt.time(9, 0), dt.time(23, 0))
EVENING = (dt.time(17, 0), dt.time(20, 0))
DAYTIME = (dt.time(9, 0), dt.time(15, 0))
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

Interval = tuple[dt.datetime, dt.datetime]


def _at(zone: ZoneInfo, day: dt.date, time: dt.time) -> dt.datetime:
    """The local wall time as a UTC instant."""
    return dt.datetime.combine(day, time, tzinfo=zone).astimezone(dt.UTC)


def _invalid_range() -> ApiError:
    return ApiError(422, "invalid_range", "Give `from` and `to` as dates or timestamps.")


def _bound(value: str, zone: ZoneInfo, *, end: bool) -> dt.datetime:
    """A date is local midnight (a date `to` includes that whole day); a timestamp is itself,
    taken as UTC when it carries no offset."""
    value = value.strip()
    try:
        day = dt.date.fromisoformat(value)
    except ValueError:
        try:
            stamp = dt.datetime.fromisoformat(value)
        except ValueError:
            raise _invalid_range() from None
        return stamp.replace(tzinfo=dt.UTC) if stamp.tzinfo is None else stamp.astimezone(dt.UTC)
    return _at(zone, day + dt.timedelta(days=1) if end else day, dt.time.min)


def _occurrence(date: dt.date, year: int) -> dt.date:
    """The date in `year`; Feb 29 falls on Feb 28 in a year that has no Feb 29."""
    if date.month == 2 and date.day == 29 and not pycalendar.isleap(year):
        return dt.date(year, 2, 28)
    return dt.date(year, date.month, date.day)


async def _couple_zone(session: AsyncSession, couple_id: uuid.UUID) -> ZoneInfo:
    couple = await session.get(Couple, couple_id)
    if couple is None:
        raise not_found("You don't have a couple space yet.")
    return zone_for(couple.timezone)


async def list_entries(
    session: AsyncSession,
    viewer_user_id: uuid.UUID,
    couple_id: uuid.UUID,
    from_: str,
    to: str,
) -> list[CalendarEntry]:
    """The visible dates that start in [from, to) and the occasions that fall in it."""
    zone = await _couple_zone(session, couple_id)
    start = _bound(from_, zone, end=False)
    end = _bound(to, zone, end=True)
    if end <= start:
        raise ApiError(422, "invalid_range", "`to` must be after `from`.")
    if end - start > MAX_RANGE:
        raise ApiError(422, "range_too_long", "Ask for 120 days or fewer at a time.")

    entries: list[CalendarEntry] = []
    query = (
        visible_itineraries(session, viewer_user_id, couple_id)
        .where(Itinerary.starts_at.is_not(None), Itinerary.starts_at >= start)
        .where(Itinerary.starts_at < end)
    )
    for itinerary in (await session.execute(query)).scalars():
        assert itinerary.starts_at is not None
        entries.append(
            CalendarEntry(
                kind="date",
                id=itinerary.id,
                title=itinerary.title,
                starts_at=itinerary.starts_at,
                ends_at=itinerary.ends_at,
                all_day=False,
                itinerary_id=itinerary.id,
                status=itinerary.status,
                is_surprise=itinerary.is_surprise,
                announced=itinerary.announced_at is not None,
            )
        )
    # A surprise nobody has revealed yet: the surprisee gets a time and no name.
    surprises = (
        upcoming_surprises(session, viewer_user_id, couple_id)
        .where(Itinerary.starts_at >= start)
        .where(Itinerary.starts_at < end)
    )
    for itinerary in (await session.execute(surprises)).scalars():
        assert itinerary.starts_at is not None
        entries.append(
            CalendarEntry(
                kind="date",
                id=itinerary.id,
                title=SURPRISE_ENTRY_TITLE,
                starts_at=itinerary.starts_at,
                ends_at=itinerary.ends_at,
                all_day=False,
                itinerary_id=itinerary.id,
                status="scheduled",
                is_surprise=True,
                announced=False,
            )
        )

    first_year, last_year = start.astimezone(zone).year, end.astimezone(zone).year
    occasions = await session.execute(
        select(ImportantDate).where(ImportantDate.couple_id == couple_id)
    )
    for occasion in occasions.scalars():
        if occasion.recurring:
            days = [
                _occurrence(occasion.date, year)
                for year in range(max(first_year, occasion.date.year), last_year + 1)
            ]
        else:
            days = [occasion.date]
        for day in days:
            day_start = _at(zone, day, dt.time.min)
            day_end = _at(zone, day + dt.timedelta(days=1), dt.time.min)
            if day_start < end and day_end > start:
                entries.append(
                    CalendarEntry(
                        kind="occasion",
                        id=occasion.id,
                        title=occasion.name,
                        starts_at=day_start,
                        ends_at=day_end,
                        all_day=True,
                    )
                )
    entries.sort(key=lambda entry: (entry.starts_at, entry.kind, entry.title))
    return entries


# --- Suggest times


def _intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for a_start, a_end in a:
        for b_start, b_end in b:
            start, end = max(a_start, b_start), min(a_end, b_end)
            if start < end:
                out.append((start, end))
    return sorted(out)


def _subtract(free: list[Interval], busy: Interval) -> list[Interval]:
    out: list[Interval] = []
    for start, end in free:
        if busy[1] <= start or busy[0] >= end:
            out.append((start, end))
            continue
        if start < busy[0]:
            out.append((start, busy[0]))
        if busy[1] < end:
            out.append((busy[1], end))
    return out


def _round_up(instant: dt.datetime, zone: ZoneInfo) -> dt.datetime:
    """The next :00 or :30 on the local clock (itself when already on one)."""
    local = instant.astimezone(zone)
    past = (local.minute % 30) * 60 + local.second + local.microsecond / 1_000_000
    if past:
        instant += dt.timedelta(seconds=STEP_SECONDS - past)
    return instant


@dataclass
class EventStart:
    """When an idea's event starts, in the couple's timezone. `time` is None when unknown;
    `date` is None when the event's date can't be read, which leaves it nowhere to be offered."""

    date: dt.date | None
    time: dt.time | None


@dataclass
class Availability:
    """One person's recurring windows by weekday and their blocked dates. `unset` means they
    have no windows at all, which counts as free 09:00-23:00 every day."""

    windows: dict[int, list[tuple[dt.time, dt.time]]]
    blocked: set[dt.date]
    unset: bool

    def free_on(self, zone: ZoneInfo, day: dt.date) -> list[Interval]:
        if day in self.blocked:
            return []
        windows = [DEFAULT_WINDOW] if self.unset else self.windows.get(day.weekday(), [])
        return sorted((_at(zone, day, start), _at(zone, day, end)) for start, end in windows)


def _earliest(
    free: list[Interval],
    duration: dt.timedelta,
    zone: ZoneInfo,
    starts: Interval | None = None,
) -> dt.datetime | None:
    """The earliest start (on :00/:30) that fits `duration` in a free interval, with the start
    inside `starts` when given."""
    for begin, end in free:
        start = max(begin, starts[0]) if starts else begin
        start = _round_up(start, zone)
        if starts and start > starts[1]:
            continue
        if start + duration <= end:
            return start
    return None


def _part_of_day(start: dt.datetime) -> str:
    return "morning" if start.hour < 12 else "afternoon" if start.hour < 17 else "evening"


def _pick_start(
    free: list[Interval],
    day: dt.date,
    zone: ZoneInfo,
    duration: dt.timedelta,
    best_time: str | None,
    event: EventStart | None,
) -> dt.datetime | None:
    """The one start for this day, or None when nothing fits."""
    weekend = day.weekday() >= 5
    if event is not None and event.time is not None:
        wanted = _at(zone, day, event.time)
        fits = any(begin <= wanted and wanted + duration <= end for begin, end in free)
        return wanted if fits else None
    if event is not None:
        return _earliest(free, duration, zone)
    if best_time == "weekend":
        return _earliest(free, duration, zone) if weekend else None
    if best_time == "evening":
        evening = (_at(zone, day, EVENING[0]), _at(zone, day, EVENING[1]))
        return _earliest(free, duration, zone, evening)
    if best_time == "day":
        daytime = (_at(zone, day, DAYTIME[0]), _at(zone, day, DAYTIME[1]))
        return _earliest(free, duration, zone, daytime)
    if weekend:  # weekend evenings rank first, so look for one before anything else
        evening = (_at(zone, day, EVENING[0]), _at(zone, day, EVENING[1]))
        start = _earliest(free, duration, zone, evening)
        if start is not None:
            return start
    return _earliest(free, duration, zone)


def compute_slots(
    zone: ZoneInfo,
    today: dt.date,
    people: list[Availability],
    busy: Iterable[Interval],
    *,
    duration_minutes: int,
    weeks: int,
    best_time: str | None,
    event: EventStart | None = None,
) -> list[TimeSlot]:
    """Up to three suggested slots, a pure function of its inputs (m3 design, "Suggest times").
    `today` is the local date; the candidate days start tomorrow."""
    duration = dt.timedelta(minutes=duration_minutes)
    buffered = [(start - BUFFER, end + BUFFER) for start, end in busy]
    anytime = best_time in (None, "any") and event is None
    found: list[tuple[int, dt.datetime, str]] = []

    for offset in range(1, weeks * 7 + 1):
        day = today + dt.timedelta(days=offset)
        if event is not None and (event.date is None or day != event.date):
            continue
        free = [(_at(zone, day, dt.time.min), _at(zone, day + dt.timedelta(days=1), dt.time.min))]
        for person in people:
            free = _intersect(free, person.free_on(zone, day))
        for block in buffered:
            free = _subtract(free, block)
        start = _pick_start(free, day, zone, duration, best_time, event) if free else None
        if start is None:
            continue

        local = start.astimezone(zone)
        lead = "Free for the event on" if event is not None else "You're both free"
        reason = f"{lead} {WEEKDAYS[day.weekday()]} {_part_of_day(local)}"
        weekend_evening = day.weekday() >= 5 and EVENING[0] <= local.time() <= EVENING[1]
        found.append((0 if anytime and weekend_evening else 1, start, reason))

    found.sort(key=lambda item: (item[0], item[1]))
    return [
        TimeSlot(starts_at=start, ends_at=start + duration, reason=reason)
        for _, start, reason in found[:MAX_SLOTS]
    ]


def _event_start(generation: dict[str, Any]) -> EventStart | None:
    event = generation.get("event")
    if not isinstance(event, dict) or not event.get("name"):
        return None
    try:
        date = dt.date.fromisoformat(str(event.get("local_date")))
    except ValueError:
        return EventStart(date=None, time=None)
    raw = event.get("local_time")
    try:
        time = dt.time.fromisoformat(str(raw)).replace(tzinfo=None) if raw else None
    except ValueError:
        time = None
    return EventStart(date=date, time=time)


async def _availability(session: AsyncSession, couple_id: uuid.UUID) -> list[Availability]:
    profile_ids = list(
        (
            await session.execute(
                select(PartnerProfile.id)
                .where(PartnerProfile.couple_id == couple_id)
                .order_by(PartnerProfile.slot)
            )
        ).scalars()
    )
    windows = (
        await session.execute(
            select(AvailabilityWindow).where(AvailabilityWindow.profile_id.in_(profile_ids))
        )
    ).scalars()
    blocks = (
        await session.execute(
            select(AvailabilityBlock).where(AvailabilityBlock.profile_id.in_(profile_ids))
        )
    ).scalars()
    by_profile = {
        profile_id: Availability(windows={}, blocked=set(), unset=True)
        for profile_id in profile_ids
    }
    for window in windows:
        person = by_profile[window.profile_id]
        person.unset = False
        person.windows.setdefault(window.weekday, []).append((window.start_time, window.end_time))
    for block in blocks:
        by_profile[block.profile_id].blocked.add(block.date)
    return list(by_profile.values())


async def suggest_times(
    session: AsyncSession,
    viewer_user_id: uuid.UUID,
    couple_id: uuid.UUID,
    body: SuggestTimesIn,
    now: dt.datetime | None = None,
) -> SuggestTimesOut:
    """Up to three times both of you are free for `body.duration_minutes`."""
    best_time: str | None = body.best_time
    event: EventStart | None = None
    if body.idea_id is not None:
        idea = await get_visible_idea(session, couple_id, viewer_user_id, body.idea_id)
        best_time = best_time or idea.best_time
        event = _event_start(idea.generation)

    zone = await _couple_zone(session, couple_id)
    today = (now or dt.datetime.now(dt.UTC)).astimezone(zone).date()
    horizon = (
        _at(zone, today, dt.time.min) - dt.timedelta(days=1),
        _at(zone, today + dt.timedelta(days=body.weeks * 7 + 2), dt.time.min),
    )
    rows = await session.execute(
        visible_itineraries(session, viewer_user_id, couple_id).where(
            Itinerary.status == "scheduled",
            Itinerary.starts_at.is_not(None),
            Itinerary.starts_at >= horizon[0],
            Itinerary.starts_at < horizon[1],
        )
    )
    # A surprise the viewer hasn't seen yet still blocks the time; only its times are used.
    surprises = await session.execute(
        upcoming_surprises(session, viewer_user_id, couple_id).where(
            Itinerary.starts_at >= horizon[0],
            Itinerary.starts_at < horizon[1],
        )
    )
    busy: list[Interval] = []
    for itinerary in [*rows.scalars(), *surprises.scalars()]:
        assert itinerary.starts_at is not None
        ends_at = itinerary.ends_at or itinerary.starts_at + DEFAULT_DATE_LENGTH
        busy.append((itinerary.starts_at, ends_at))

    slots = compute_slots(
        zone,
        today,
        await _availability(session, couple_id),
        busy,
        duration_minutes=body.duration_minutes,
        weeks=body.weeks,
        best_time=best_time,
        event=event,
    )
    return SuggestTimesOut(slots=slots)
