"""The calendar API: visible dates and occasions in a range, and deterministic time suggestions."""

import datetime as dt
import uuid
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from sqlalchemy import update

from app.models import (
    AvailabilityBlock,
    AvailabilityWindow,
    Couple,
    Idea,
    ImportantDate,
)
from app.schemas.calendar import TimeSlot
from app.services.calendar import Availability, EventStart, _event_start, compute_slots
from tests.authutil import browser, call, profile_ids
from tests.itinerary_helpers import TAQUERIA, World, seed, when, world

pytestmark = pytest.mark.db

CALENDAR = "/api/v1/calendar"
SUGGEST = f"{CALENDAR}/suggest-times"
UTC = ZoneInfo("UTC")
NEW_YORK = ZoneInfo("America/New_York")
# A Monday, so the candidate days run Tue 2 June onwards: Sat 6 and Sun 7 are the first weekend.
TODAY = dt.date(2026, 6, 1)


async def set_timezone(w: World, name: str) -> None:
    async with w.app.state.sessionmaker() as session:
        await session.execute(update(Couple).where(Couple.id == w.couple_id).values(timezone=name))
        await session.commit()


async def add_occasion(w: World, name: str, date: dt.date, *, recurring: bool) -> uuid.UUID:
    async with w.app.state.sessionmaker() as session:
        occasion = ImportantDate(
            couple_id=w.couple_id,
            name=name,
            date=date,
            recurring=recurring,
            kind="custom",
            created_by_user_id=w.owner_id,
        )
        session.add(occasion)
        await session.commit()
        return occasion.id


async def add_availability(
    w: World,
    slot: str,
    *,
    windows: list[tuple[int, dt.time, dt.time]] = (),  # type: ignore[assignment]
    blocks: list[dt.date] = (),  # type: ignore[assignment]
) -> None:
    profile_id = uuid.UUID((await profile_ids(w.owner))[slot])
    async with w.app.state.sessionmaker() as session:
        for weekday, start, end in windows:
            session.add(
                AvailabilityWindow(
                    profile_id=profile_id, weekday=weekday, start_time=start, end_time=end
                )
            )
        for date in blocks:
            session.add(AvailabilityBlock(profile_id=profile_id, date=date))
        await session.commit()


async def add_idea(w: World, **fields: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "couple_id": w.couple_id,
        "created_by_user_id": w.owner_id,
        "source": "manual",
        "visibility": "shared",
        "title": "An idea",
        "pitch": "",
    }
    values.update(fields)
    async with w.app.state.sessionmaker() as session:
        idea = Idea(**values)
        session.add(idea)
        await session.commit()
        return idea.id


def utc(day: int, hour: int, minute: int = 0, month: int = 6) -> dt.datetime:
    return dt.datetime(2026, month, day, hour, minute, tzinfo=dt.UTC)


def slots(
    people: list[Availability] | None = None,
    busy: list[tuple[dt.datetime, dt.datetime]] | None = None,
    *,
    duration_minutes: int = 60,
    weeks: int = 3,
    best_time: str | None = None,
    event: EventStart | None = None,
    open_periods: list[dict[str, Any]] | None = None,
    zone: ZoneInfo = UTC,
    today: dt.date = TODAY,
) -> list[TimeSlot]:
    return compute_slots(
        zone,
        today,
        people if people is not None else [unset(), unset()],
        busy or [],
        duration_minutes=duration_minutes,
        weeks=weeks,
        best_time=best_time,
        event=event,
        open_periods=open_periods,
    )


def unset() -> Availability:
    return Availability(windows={}, blocked=set(), unset=True)


def weekly(
    weekday: int, start: dt.time, end: dt.time, blocked: set[dt.date] | None = None
) -> Availability:
    return Availability(windows={weekday: [(start, end)]}, blocked=blocked or set(), unset=False)


def starts(result: list[TimeSlot]) -> list[dt.datetime]:
    return [slot.starts_at for slot in result]


# --- GET /calendar


async def test_calendar_lists_only_visible_dates_sorted(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        late = await seed(
            w, title="Late", status="scheduled", announced_at=when(-1), starts_at=when(8)
        )
        early = await seed(
            w, title="Early", status="scheduled", announced_at=when(-1), starts_at=when(3)
        )
        draft = await seed(w, title="Draft", starts_at=when(4))
        unannounced = await seed(w, title="Unannounced", status="scheduled", starts_at=when(5))
        surprise = await seed(
            w,
            title="Surprise",
            status="scheduled",
            announced_at=when(-1),
            is_surprise=True,
            starts_at=when(6),
        )
        await seed(w, title="No start", status="scheduled", announced_at=when(-1))
        await seed(
            w, title="Out of range", status="scheduled", announced_at=when(-1), starts_at=when(40)
        )
        params = {"from": when(0).isoformat(), "to": when(20).isoformat()}

        mine = (await w.owner.get(CALENDAR, params=params)).json()
        assert [e["title"] for e in mine] == ["Early", "Draft", "Unannounced", "Surprise", "Late"]
        first = mine[0]
        assert first["kind"] == "date" and first["all_day"] is False
        assert first["id"] == first["itinerary_id"] == str(early)
        assert first["status"] == "scheduled"
        assert first["is_surprise"] is False and first["announced"] is True
        assert mine[1]["announced"] is False

        theirs = (await w.partner.get(CALENDAR, params=params)).json()
        # An unrevealed surprise is on their calendar, redacted (its title never leaves).
        assert [e["title"] for e in theirs] == ["Early", "Surprise date ✨", "Late"]
        assert "Surprise" not in {e["title"] for e in theirs}
        assert {str(draft), str(unannounced)}.isdisjoint({e["id"] for e in theirs})
        assert str(surprise) in {e["id"] for e in theirs}
        assert str(late) in {e["id"] for e in theirs}


async def test_calendar_sorts_dates_and_occasions_together_by_start(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_timezone(w, "UTC")
        await seed(
            w, title="Date six", status="scheduled", announced_at=when(-1), starts_at=when(6)
        )
        await seed(
            w, title="Date three", status="scheduled", announced_at=when(-1), starts_at=when(3)
        )
        await add_occasion(w, "Occasion four", when(4).date(), recurring=False)
        await add_occasion(w, "Occasion two", when(2).date(), recurring=False)
        params = {"from": when(0).date().isoformat(), "to": when(8).date().isoformat()}
        entries = (await w.partner.get(CALENDAR, params=params)).json()
        assert [e["title"] for e in entries] == [
            "Occasion two",
            "Date three",
            "Occasion four",
            "Date six",
        ]
        assert [e["kind"] for e in entries] == ["occasion", "date", "occasion", "date"]
        assert [e["starts_at"] for e in entries] == sorted(e["starts_at"] for e in entries)


async def test_calendar_occasions_expand_yearly_in_the_couple_timezone(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_timezone(w, "America/New_York")
        anniversary = await add_occasion(w, "Anniversary", dt.date(2020, 6, 15), recurring=True)
        await add_occasion(w, "One off", dt.date(2026, 6, 20), recurring=False)
        await add_occasion(w, "Elsewhen", dt.date(2026, 9, 1), recurring=False)
        await add_occasion(w, "Not yet", dt.date(2030, 6, 16), recurring=True)

        entries = (
            await w.partner.get(CALENDAR, params={"from": "2026-06-01", "to": "2026-06-30"})
        ).json()
        assert [e["title"] for e in entries] == ["Anniversary", "One off"]
        first = entries[0]
        assert first["kind"] == "occasion" and first["all_day"] is True
        assert first["id"] == str(anniversary)
        assert first["itinerary_id"] is None and first["status"] is None
        # Local midnight in New York (EDT, UTC-4) to the next one.
        assert first["starts_at"].startswith("2026-06-15T04:00:00")
        assert first["ends_at"].startswith("2026-06-16T04:00:00")

        later = (
            await w.owner.get(CALENDAR, params={"from": "2027-06-01", "to": "2027-06-30"})
        ).json()
        assert [e["title"] for e in later] == ["Anniversary"]
        assert later[0]["starts_at"].startswith("2027-06-15T04:00:00")
        before = (
            await w.owner.get(CALENDAR, params={"from": "2019-06-01", "to": "2019-06-30"})
        ).json()
        assert before == []


async def test_calendar_feb_29_falls_on_feb_28_in_other_years(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_timezone(w, "UTC")
        await add_occasion(w, "Leap", dt.date(2024, 2, 29), recurring=True)

        def span(year: int) -> dict[str, str]:
            return {"from": f"{year}-02-01", "to": f"{year}-03-31"}

        in_2027 = (await w.owner.get(CALENDAR, params=span(2027))).json()
        assert [e["starts_at"][:10] for e in in_2027] == ["2027-02-28"]
        in_2028 = (await w.owner.get(CALENDAR, params=span(2028))).json()
        assert [e["starts_at"][:10] for e in in_2028] == ["2028-02-29"]


async def test_calendar_range_rules(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        ok = await w.owner.get(CALENDAR, params={"from": "2026-01-01", "to": "2026-04-30"})
        assert ok.status_code == 200, ok.text
        stamps = {"from": "2026-01-01T00:00:00Z", "to": "2026-05-01T00:00:00Z"}
        assert (await w.owner.get(CALENDAR, params=stamps)).status_code == 200

        long = await w.owner.get(CALENDAR, params={"from": "2026-01-01", "to": "2026-05-01"})
        assert long.status_code == 422
        assert long.json()["error"]["code"] == "range_too_long"
        past_end = {"from": "2026-01-01T00:00:00Z", "to": "2026-05-01T00:00:01Z"}
        assert (await w.owner.get(CALENDAR, params=past_end)).status_code == 422

        backwards = {"from": "2026-02-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"}
        assert (await w.owner.get(CALENDAR, params=backwards)).status_code == 422
        same = {"from": "2026-02-01T00:00:00Z", "to": "2026-02-01T00:00:00Z"}
        assert (await w.owner.get(CALENDAR, params=same)).status_code == 422
        assert (
            await w.owner.get(CALENDAR, params={"from": "soon", "to": "2026-01-01"})
        ).status_code == 422
        assert (await w.owner.get(CALENDAR, params={"from": "2026-01-01"})).status_code == 422


async def test_calendar_needs_a_signed_in_member(db_app: FastAPI) -> None:
    async with browser(db_app) as anonymous:
        assert (
            await anonymous.get(CALENDAR, params={"from": "2026-01-01", "to": "2026-01-31"})
        ).status_code == 401
        body = {"duration_minutes": 60}
        assert (await call(anonymous, "POST", SUGGEST, body)).status_code in (401, 403)


# --- POST /calendar/suggest-times: the pure rules


def test_free_both_default_ranks_weekend_evenings_first_then_earliest() -> None:
    result = slots(weeks=1)
    assert starts(result) == [utc(6, 17), utc(7, 17), utc(2, 9)]
    assert starts(slots()) == [utc(6, 17), utc(7, 17), utc(13, 17)]
    assert all(slot.ends_at - slot.starts_at == dt.timedelta(minutes=60) for slot in result)
    assert result[0].reason == "You're both free Saturday evening"
    assert result[2].reason == "You're both free Tuesday morning"
    assert slots(weeks=1, best_time="any") == result


def test_slots_are_deterministic_at_most_three_and_one_per_day() -> None:
    first = slots(weeks=6)
    assert len(first) == 3
    assert first == slots(weeks=6)
    day_starts = [slot.starts_at.date() for slot in slots(best_time="day", weeks=6)]
    assert len(day_starts) == len(set(day_starts))
    assert starts(slots(best_time="day", weeks=6)) == [utc(2, 9), utc(3, 9), utc(4, 9)]


def test_free_windows_are_the_intersection_of_both_people() -> None:
    both = [weekly(1, dt.time(10), dt.time(14)), weekly(1, dt.time(12), dt.time(18))]
    result = slots(both, weeks=2)
    assert starts(result) == [utc(2, 12), utc(9, 12)]
    assert starts(slots(both, duration_minutes=180, weeks=2)) == []
    # A person with windows only on other days leaves no free day at all.
    assert slots([weekly(1, dt.time(10), dt.time(14)), weekly(2, dt.time(10), dt.time(14))]) == []


def test_adjacent_and_overlapping_windows_are_one_free_stretch() -> None:
    inside = weekly(1, dt.time(19), dt.time(22))
    for first, second in [
        ((dt.time(18), dt.time(20)), (dt.time(20), dt.time(23))),  # adjacent
        ((dt.time(18), dt.time(21)), (dt.time(20), dt.time(23))),  # overlapping
    ]:
        split = Availability(windows={1: [first, second]}, blocked=set(), unset=False)
        result = slots([split, inside], duration_minutes=180, weeks=1)
        assert starts(result) == [utc(2, 19)]


def test_a_blocked_date_for_either_person_removes_the_day() -> None:
    blocked = weekly(1, dt.time(10), dt.time(14), {dt.date(2026, 6, 2)})
    other = weekly(1, dt.time(10), dt.time(14))
    assert starts(slots([blocked, other], weeks=2)) == [utc(9, 10)]
    assert starts(slots([other, blocked], weeks=2)) == [utc(9, 10)]
    default_blocked = Availability(windows={}, blocked={dt.date(2026, 6, 6)}, unset=True)
    assert utc(6, 17) not in starts(slots([default_blocked, unset()]))


def test_existing_dates_block_an_hour_either_side() -> None:
    # 18:00-19:00 on Tuesday: free until 17:00 and from 20:00.
    busy = [(utc(2, 18), utc(2, 19))]
    assert starts(slots(busy=busy, best_time="evening", weeks=1))[:1] == [utc(2, 20)]
    # 18:00-20:00 pushes the free time to 21:00, past the evening start range, so Tuesday is out.
    wider = [(utc(2, 18), utc(2, 20))]
    evenings = slots(busy=wider, best_time="evening", weeks=1)
    assert utc(2, 21) not in starts(evenings) and all(s.starts_at.day != 2 for s in evenings)
    # A date 08:15-09:15 leaves 10:15, rounded up to 10:30.
    morning = slots(busy=[(utc(2, 8, 15), utc(2, 9, 15))], weeks=1)
    assert utc(2, 10, 30) in starts(morning)
    # The window must still hold the whole duration.
    tight = slots(busy=[(utc(2, 21), utc(2, 22))], duration_minutes=120, best_time="day", weeks=1)
    assert utc(2, 9) in starts(tight)


def test_evening_day_and_weekend_rules() -> None:
    evening = slots(best_time="evening", weeks=1)
    assert starts(evening) == [utc(2, 17), utc(3, 17), utc(4, 17)]
    # The start may be as late as 20:00 when that is what is free.
    late = [(utc(2, 9), utc(2, 17))]  # a date 09:00-17:00 leaves 18:00 onwards
    assert starts(slots(busy=late, best_time="evening", weeks=1))[0] == utc(2, 18)
    # `day` starts between 09:00 and 15:00.
    after_nine = slots(busy=[(utc(2, 8), utc(2, 10))], best_time="day", weeks=1)
    assert after_nine[0].starts_at == utc(2, 11)
    only_late = slots([weekly(1, dt.time(16), dt.time(22))] * 2, best_time="day", weeks=1)
    assert only_late == []
    weekend = slots(best_time="weekend", weeks=2)
    assert starts(weekend) == [utc(6, 9), utc(7, 9), utc(13, 9)]
    assert {slot.starts_at.weekday() for slot in weekend} <= {5, 6}


def test_an_event_offers_only_its_own_start() -> None:
    event = EventStart(date=dt.date(2026, 6, 10), time=dt.time(19, 30))
    result = slots(event=event, duration_minutes=120, best_time="day")
    assert starts(result) == [utc(10, 19, 30)]
    assert result[0].reason == "Free for the event on Wednesday evening"
    clash = slots(event=event, busy=[(utc(10, 18), utc(10, 19))], duration_minutes=120)
    assert clash == []  # the buffer reaches 20:00
    assert slots(event=EventStart(date=dt.date(2026, 8, 1), time=dt.time(19)), weeks=3) == []
    assert slots(event=EventStart(date=TODAY, time=dt.time(19))) == []  # today is not offered
    no_time = slots(event=EventStart(date=dt.date(2026, 6, 10), time=None))
    assert starts(no_time) == [utc(10, 9)]


def test_an_event_without_a_time_gets_only_its_day_at_the_earliest_free_start() -> None:
    event = EventStart(date=dt.date(2026, 6, 10), time=None)
    busy = [(utc(10, 8), utc(10, 10))]  # the buffer reaches 11:00
    assert starts(slots(event=event, busy=busy)) == [utc(10, 11)]
    assert starts(slots(event=event, weeks=6)) == [utc(10, 9)]


def test_an_event_with_an_unreadable_date_gets_no_slots() -> None:
    for bad in ({"name": "A gig", "local_date": "soon"}, {"name": "A gig"}):
        event = _event_start({"event": bad})
        assert event is not None and event.date is None
        assert slots(event=event, weeks=6) == []
    # An unreadable time only drops the time: the date still gets the earliest free start.
    event = _event_start(
        {"event": {"name": "A gig", "local_date": "2026-06-10", "local_time": "x"}}
    )
    assert event == EventStart(date=dt.date(2026, 6, 10), time=None)
    assert _event_start({"event": {"local_date": "2026-06-10"}}) is None
    assert _event_start({}) is None


def test_the_horizon_runs_from_tomorrow_to_today_plus_weeks_times_seven() -> None:
    monday = [weekly(0, dt.time(10), dt.time(14))] * 2  # 1, 8 and 15 June
    assert starts(slots(monday, weeks=1)) == [utc(8, 10)]  # today (1 June) is not offered
    assert starts(slots(monday, weeks=2)) == [utc(8, 10), utc(15, 10)]
    tuesday = [weekly(1, dt.time(10), dt.time(14))] * 2  # 2 and 9 June
    assert starts(slots(tuesday, weeks=1)) == [utc(2, 10)]  # 9 June is one day past the end
    assert starts(slots(tuesday, weeks=2)) == [utc(2, 10), utc(9, 10)]


def test_a_day_with_a_dst_change_keeps_its_real_length() -> None:
    # Sunday 8 March 2026: New York springs forward, so 01:00-04:00 local is only two hours.
    window = [weekly(6, dt.time(1), dt.time(4))] * 2
    today = dt.date(2026, 3, 7)
    fits = slots(window, zone=NEW_YORK, today=today, weeks=1, duration_minutes=120)
    assert starts(fits) == [dt.datetime(2026, 3, 8, 6, 0, tzinfo=dt.UTC)]  # 01:00 EST
    assert fits[0].ends_at == dt.datetime(2026, 3, 8, 8, 0, tzinfo=dt.UTC)  # 04:00 EDT
    too_long = slots(window, zone=NEW_YORK, today=today, weeks=1, duration_minutes=150)
    assert too_long == []
    # After the change an evening is at the new offset.
    evening = slots(zone=NEW_YORK, today=today, weeks=1, best_time="evening")
    assert evening[0].starts_at == dt.datetime(2026, 3, 8, 21, 0, tzinfo=dt.UTC)  # 17:00 EDT


# --- POST /calendar/suggest-times: the route


async def test_suggest_times_uses_both_profiles_availability(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_timezone(w, "UTC")
        await add_availability(
            w, "owner", windows=[(d, dt.time(18), dt.time(22)) for d in range(7)]
        )
        await add_availability(
            w, "partner", windows=[(d, dt.time(20), dt.time(23)) for d in range(7)]
        )
        response = await call(w.owner, "POST", SUGGEST, {"duration_minutes": 90})
        assert response.status_code == 200, response.text
        body = response.json()["slots"]
        assert len(body) == 3
        for slot in body:
            start = dt.datetime.fromisoformat(slot["starts_at"])
            end = dt.datetime.fromisoformat(slot["ends_at"])
            assert end - start == dt.timedelta(minutes=90)
            assert (start.hour, start.minute) == (20, 0)  # the overlap is 20:00-22:00
            assert slot["reason"].startswith("You're both free ")
        again = await call(w.partner, "POST", SUGGEST, {"duration_minutes": 90})
        assert again.json()["slots"] == body


async def test_suggest_times_skips_blocks_and_visible_dates(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_timezone(w, "UTC")
        tomorrow = dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)
        await add_availability(w, "partner", blocks=[tomorrow + dt.timedelta(days=1)])
        at = dt.datetime.combine(tomorrow, dt.time(10), tzinfo=dt.UTC)
        await seed(
            w,
            status="scheduled",
            announced_at=when(-1),
            starts_at=at,
            ends_at=at + dt.timedelta(hours=2),
        )
        # The planner's unrevealed surprise blocks its time for the surprisee too (times only;
        # the calendar already shows them a "Surprise date" entry there).
        await seed(
            w,
            title="Surprise",
            status="scheduled",
            announced_at=when(-1),
            is_surprise=True,
            starts_at=at + dt.timedelta(days=2),
            ends_at=at + dt.timedelta(days=2, hours=2),
        )
        body = {"duration_minutes": 120, "weeks": 1, "best_time": "day"}
        mine = (await call(w.owner, "POST", SUGGEST, body)).json()["slots"]
        starts_mine = [dt.datetime.fromisoformat(s["starts_at"]) for s in mine]
        # Tomorrow is free from 13:00 (10:00-12:00 plus an hour); the day after is blocked.
        assert starts_mine[0] == dt.datetime.combine(tomorrow, dt.time(13), tzinfo=dt.UTC)
        assert all(s.date() != tomorrow + dt.timedelta(days=1) for s in starts_mine)
        assert (
            dt.datetime.combine(tomorrow + dt.timedelta(days=2), dt.time(9), tzinfo=dt.UTC)
            not in starts_mine
        )

        theirs = (await call(w.partner, "POST", SUGGEST, body)).json()["slots"]
        starts_theirs = [dt.datetime.fromisoformat(s["starts_at"]) for s in theirs]
        assert (
            dt.datetime.combine(tomorrow + dt.timedelta(days=2), dt.time(9), tzinfo=dt.UTC)
            not in starts_theirs
        )
        assert all("Surprise" not in str(s) for s in theirs)


async def test_suggest_times_a_date_without_an_end_blocks_three_hours(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_timezone(w, "UTC")
        tomorrow = dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)
        at = dt.datetime.combine(tomorrow, dt.time(10), tzinfo=dt.UTC)
        await seed(w, status="scheduled", announced_at=when(-1), starts_at=at, ends_at=None)
        body = {"duration_minutes": 60, "weeks": 1, "best_time": "day"}
        result = (await call(w.owner, "POST", SUGGEST, body)).json()["slots"]
        # 10:00 plus three hours, plus the buffer either side: busy 09:00-14:00.
        assert dt.datetime.fromisoformat(result[0]["starts_at"]) == at.replace(hour=14)


async def test_suggest_times_an_event_with_an_unreadable_date_has_no_slots(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        idea = await add_idea(w, generation={"event": {"name": "A gig", "local_date": "soon"}})
        body = {"duration_minutes": 60, "idea_id": str(idea), "weeks": 6}
        response = await call(w.owner, "POST", SUGGEST, body)
        assert response.status_code == 200, response.text
        assert response.json() == {"slots": []}


async def test_suggest_times_takes_best_time_and_event_from_the_idea(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_timezone(w, "America/New_York")
        evening_idea = await add_idea(w, best_time="evening")
        body = {"duration_minutes": 60, "idea_id": str(evening_idea)}
        result = (await call(w.owner, "POST", SUGGEST, body)).json()["slots"]
        assert len(result) == 3
        for slot in result:
            local = dt.datetime.fromisoformat(slot["starts_at"]).astimezone(NEW_YORK)
            assert dt.time(17) <= local.time() <= dt.time(20)
        # The request's own best_time wins over the idea's.
        override = {**body, "best_time": "day"}
        day = (await call(w.owner, "POST", SUGGEST, override)).json()["slots"]
        for slot in day:
            local = dt.datetime.fromisoformat(slot["starts_at"]).astimezone(NEW_YORK)
            assert dt.time(9) <= local.time() <= dt.time(15)

        event_day = dt.datetime.now(NEW_YORK).date() + dt.timedelta(days=9)
        event_idea = await add_idea(
            w,
            title="A gig",
            generation={
                "event": {
                    "name": "A gig",
                    "local_date": event_day.isoformat(),
                    "local_time": "20:30:00",
                }
            },
        )
        gig = {"duration_minutes": 120, "idea_id": str(event_idea), "weeks": 2}
        slots_out = (await call(w.owner, "POST", SUGGEST, gig)).json()["slots"]
        assert len(slots_out) == 1
        start = dt.datetime.fromisoformat(slots_out[0]["starts_at"]).astimezone(NEW_YORK)
        assert (start.date(), start.time()) == (event_day, dt.time(20, 30))


async def test_suggest_times_idea_must_be_visible_to_the_requester(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        private = await add_idea(w, visibility="private")
        body = {"duration_minutes": 60, "idea_id": str(private)}
        assert (await call(w.owner, "POST", SUGGEST, body)).status_code == 200
        hidden = await call(w.partner, "POST", SUGGEST, body)
        assert hidden.status_code == 404
        assert hidden.json()["error"]["code"] == "not_found"
        missing = {"duration_minutes": 60, "idea_id": str(uuid.uuid4())}
        assert (await call(w.owner, "POST", SUGGEST, missing)).status_code == 404


async def test_suggest_times_validates_the_request(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        for body in (
            {"duration_minutes": 29},
            {"duration_minutes": 721},
            {"duration_minutes": 60, "weeks": 0},
            {"duration_minutes": 60, "weeks": 7},
            {"duration_minutes": 60, "best_time": "noon"},
            {},
        ):
            response = await call(w.owner, "POST", SUGGEST, body)
            assert response.status_code == 422, body
        edges = {"duration_minutes": 720, "weeks": 6}
        assert (await call(w.owner, "POST", SUGGEST, edges)).status_code == 200


# --- Venue opening hours (FR-CAL-4)


def open_hours(opens: int, closes: int) -> list[dict[str, dict[str, int]]]:
    return [
        {"open": {"day": day, "hour": opens, "minute": 0}, "close": {"day": day, "hour": closes}}
        for day in range(7)
    ]


def test_slots_stay_inside_the_venue_opening_hours() -> None:
    # Open 12:00-15:00: only the afternoon is on offer.
    result = slots(duration_minutes=120, open_periods=open_hours(12, 15))
    assert len(result) == 3
    for slot in result:
        assert dt.time(12) <= slot.starts_at.time()
        assert slot.ends_at.time() <= dt.time(15)


def test_a_slot_that_would_run_past_closing_is_not_offered() -> None:
    assert slots(duration_minutes=240, open_periods=open_hours(12, 15)) == []


def test_a_best_time_the_venue_is_closed_for_gives_no_slot() -> None:
    assert slots(best_time="evening", open_periods=open_hours(10, 15)) == []


def test_unknown_hours_change_nothing() -> None:
    assert slots(open_periods=None) == slots()


def test_slots_follow_hours_that_wrap_past_midnight() -> None:
    late = [
        {"open": {"day": day, "hour": 18, "minute": 0}, "close": {"day": (day + 1) % 7, "hour": 2}}
        for day in range(7)
    ]
    result = slots(duration_minutes=60, best_time="evening", open_periods=late)
    assert len(result) == 3
    assert all(slot.starts_at.time() >= dt.time(18) for slot in result)


async def test_suggest_times_avoid_the_ideas_venue_closed_hours(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_timezone(w, "UTC")
        # Taqueria Sol is open 10:00-21:00; both are free 20:00-23:00, so only 20:00 fits an hour.
        for who in ("owner", "partner"):
            await add_availability(
                w, who, windows=[(d, dt.time(20), dt.time(23)) for d in range(7)]
            )
        idea = await add_idea(w, grounded=True, place_ids=[TAQUERIA])
        body = {"duration_minutes": 60, "idea_id": str(idea)}
        response = await call(w.owner, "POST", SUGGEST, body)
        assert response.status_code == 200, response.text
        found = response.json()["slots"]
        assert len(found) == 3
        for slot in found:
            assert dt.datetime.fromisoformat(slot["starts_at"]).time() == dt.time(20)
            assert dt.datetime.fromisoformat(slot["ends_at"]).time() <= dt.time(21)

        # 90 minutes would run past closing, though both are free until 23:00.
        long_ask = await call(w.owner, "POST", SUGGEST, {**body, "duration_minutes": 90})
        assert long_ask.json() == {"slots": []}

        # An idea with no venue is not constrained.
        plain = await add_idea(w)
        free = await call(w.owner, "POST", SUGGEST, {"duration_minutes": 90, "idea_id": str(plain)})
        assert len(free.json()["slots"]) == 3
