"""validate_itinerary: every check, with a passing and a failing case."""

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from app.ai.schemas import DraftStop, ItineraryDraft
from app.ai.validate import ValidationContext, validate_itinerary
from app.providers.events.fake import FakeEvents
from app.providers.places.fake import DEMO_CENTER, FakePlaces
from app.providers.types import Event, Place, ProviderUnavailable

TRATTORIA = "demo-place-la-trattoria"  # Tue-Sun 11:00-22:00 (Fri/Sat to 23:00), shut Mondays
PARK = "demo-place-riverside-park"  # always open
MONDAY = dt.date(2030, 6, 3)
FRIDAY = dt.date(2030, 6, 7)


def at(day: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=dt.UTC)


def stop(
    start: dt.datetime, minutes: int = 60, *, place_id: str | None = PARK, **kw: Any
) -> DraftStop:
    values: dict[str, Any] = {
        "title": "A stop",
        "place_id": place_id,
        "evergreen": False,
        "starts_at": start,
        "ends_at": start + dt.timedelta(minutes=minutes),
        "description": "Something lovely.",
    }
    values.update(kw)
    return DraftStop(**values)


def draft(*stops: DraftStop) -> ItineraryDraft:
    return ItineraryDraft(title="Date", stops=list(stops), transport_mode="drive")


async def places() -> dict[str, Place]:
    fake = FakePlaces()
    return {pid: await fake.details(pid) for pid in (TRATTORIA, PARK)}


async def make_ctx(**overrides: Any) -> ValidationContext:
    ctx = ValidationContext(
        seen_places=await places(),
        seen_events={},
        places=FakePlaces(),
        timezone="UTC",
        budget_max=None,
        travel_minutes=[],
        buffer_minutes=10,
        window_start=at(FRIDAY, 17),
        window_end=at(FRIDAY, 23),
    )
    return replace(ctx, **overrides)


async def codes(d: ItineraryDraft, ctx: ValidationContext) -> list[tuple[int | None, str]]:
    return [(i.stop_index, i.code) for i in await validate_itinerary(d, ctx)]


async def test_a_clean_itinerary_has_no_issues() -> None:
    ctx = await make_ctx(travel_minutes=[15])
    d = draft(
        stop(at(FRIDAY, 18), place_id=TRATTORIA),
        stop(at(FRIDAY, 19, 30), place_id=PARK),
    )
    assert await validate_itinerary(d, ctx) == []


# --- ungrounded ---------------------------------------------------------------------------


async def test_unknown_place_id_is_unknown_place() -> None:
    ctx = await make_ctx()
    assert await codes(draft(stop(at(FRIDAY, 18), place_id="invented-bistro")), ctx) == [
        (0, "unknown_place")
    ]


async def test_stop_without_any_id_is_ungrounded_unless_evergreen() -> None:
    ctx = await make_ctx()
    assert await codes(draft(stop(at(FRIDAY, 18), place_id=None)), ctx) == [(0, "ungrounded")]
    assert await codes(draft(stop(at(FRIDAY, 18), place_id=None, evergreen=True)), ctx) == []


async def test_place_not_seen_but_resolvable_by_details_is_grounded() -> None:
    ctx = await make_ctx(seen_places={})
    d = draft(stop(at(FRIDAY, 18), place_id=TRATTORIA))
    assert await validate_itinerary(d, ctx) == []
    assert TRATTORIA in ctx.seen_places  # remembered for the caller


class _Down:
    async def details(self, place_id: str) -> Place:
        raise ProviderUnavailable("places", "down")


@pytest.mark.parametrize("provider", [None, _Down()])
async def test_unresolvable_when_provider_missing_or_down(provider: Any) -> None:
    ctx = await make_ctx(seen_places={}, places=provider)
    assert await codes(draft(stop(at(FRIDAY, 18), place_id=TRATTORIA)), ctx) == [
        (0, "unknown_place")
    ]


async def _an_event(day: dt.date = FRIDAY) -> Event:
    found = await FakeEvents().search(DEMO_CENTER, 20, at(day, 0), at(day, 23, 59))
    assert found
    return found[0]


async def test_seen_event_grounds_a_stop() -> None:
    event = await _an_event()
    ctx = await make_ctx(seen_events={event.event_id: event})
    d = draft(stop(at(FRIDAY, 18), place_id=None, event_id=event.event_id))
    assert await validate_itinerary(d, ctx) == []
    other = draft(stop(at(FRIDAY, 18), place_id=None, event_id="demo-event-made-up"))
    assert await codes(other, ctx) == [(0, "unknown_event")]


async def test_evergreen_stop_must_not_carry_ids() -> None:
    event = await _an_event()
    ctx = await make_ctx(seen_events={event.event_id: event})
    for kw in (
        {"place_id": PARK},
        {"place_id": None, "event_id": event.event_id},
        {"place_id": "invented-bistro"},
    ):
        d = draft(stop(at(FRIDAY, 18), evergreen=True, **kw))
        assert await codes(d, ctx) == [(0, "evergreen_with_id")]


async def test_unknown_place_id_beside_a_valid_event_is_flagged() -> None:
    event = await _an_event()
    ctx = await make_ctx(seen_events={event.event_id: event})
    d = draft(stop(at(FRIDAY, 18), place_id="invented-bistro", event_id=event.event_id))
    assert await codes(d, ctx) == [(0, "unknown_place")]


async def test_unknown_event_id_beside_a_valid_place_is_flagged() -> None:
    ctx = await make_ctx()
    d = draft(stop(at(FRIDAY, 18), place_id=PARK, event_id="demo-event-made-up"))
    assert await codes(d, ctx) == [(0, "unknown_event")]


# --- closed -------------------------------------------------------------------------------


async def test_closed_on_a_day_the_venue_is_shut() -> None:
    ctx = await make_ctx(window_start=at(MONDAY, 17), window_end=at(MONDAY, 23))
    d = draft(stop(at(MONDAY, 18), place_id=TRATTORIA))
    assert await codes(d, ctx) == [(0, "closed")]


async def test_closed_when_the_stop_runs_past_closing_time() -> None:
    ctx = await make_ctx(window_end=at(FRIDAY + dt.timedelta(days=1), 2))
    # Friday closes at 23:00: 22:00-23:00 is fine, 22:30-23:30 is not.
    assert await codes(draft(stop(at(FRIDAY, 22), place_id=TRATTORIA)), ctx) == []
    assert await codes(draft(stop(at(FRIDAY, 22, 30), place_id=TRATTORIA)), ctx) == [(0, "closed")]


async def test_closed_uses_the_couple_timezone() -> None:
    # 23:00 UTC is 18:00 in Chicago (June): open for dinner. 15:00 UTC is 10:00 there: shut.
    ctx = await make_ctx(
        timezone="America/Chicago",
        window_start=at(FRIDAY, 0),
        window_end=at(FRIDAY, 23) + dt.timedelta(hours=3),
    )
    assert await codes(draft(stop(at(FRIDAY, 23), place_id=TRATTORIA)), ctx) == []
    assert await codes(draft(stop(at(FRIDAY, 15), place_id=TRATTORIA)), ctx) == [(0, "closed")]


async def test_closed_when_the_venue_shuts_in_the_middle_of_the_stop() -> None:
    seen = await places()
    split = [
        {"open": {"day": 5, "hour": 17}, "close": {"day": 5, "hour": 19}},
        {"open": {"day": 5, "hour": 21}, "close": {"day": 5, "hour": 23}},
    ]
    seen[TRATTORIA] = replace(seen[TRATTORIA], opening_periods=split)
    ctx = await make_ctx(seen_places=seen)
    assert await codes(draft(stop(at(FRIDAY, 17), 120, place_id=TRATTORIA)), ctx) == []
    long_stop = draft(stop(at(FRIDAY, 18), 240, place_id=TRATTORIA))
    assert await codes(long_stop, ctx) == [(0, "closed")]


async def test_unknown_opening_hours_are_not_closed() -> None:
    seen = await places()
    seen[TRATTORIA] = replace(seen[TRATTORIA], opening_periods=None)
    ctx = await make_ctx(seen_places=seen)
    assert await codes(draft(stop(at(MONDAY, 18), place_id=TRATTORIA)), ctx) == [
        (0, "outside_window")
    ]  # only the window complaint; hours unknown means no "closed"


# --- tight_travel -------------------------------------------------------------------------


async def test_tight_travel_when_the_gap_is_shorter_than_travel_plus_buffer() -> None:
    d = draft(stop(at(FRIDAY, 18)), stop(at(FRIDAY, 19, 20)))  # 20 min gap
    ok = await make_ctx(travel_minutes=[10])  # needs 20: exactly enough
    assert await validate_itinerary(d, ok) == []
    tight = await make_ctx(travel_minutes=[11])  # needs 21
    assert await codes(d, tight) == [(1, "tight_travel")]


async def test_unknown_travel_time_is_not_checked() -> None:
    d = draft(stop(at(FRIDAY, 18)), stop(at(FRIDAY, 19)))
    assert await validate_itinerary(d, await make_ctx(travel_minutes=[None])) == []
    assert await validate_itinerary(d, await make_ctx(travel_minutes=[])) == []


# --- over_budget --------------------------------------------------------------------------


async def test_over_budget_is_reported_on_the_whole_itinerary() -> None:
    d = draft(stop(at(FRIDAY, 18), est_cost=60), stop(at(FRIDAY, 20), est_cost=45.5))
    over = await make_ctx(budget_max=Decimal("100"), travel_minutes=[None])
    assert await codes(d, over) == [(None, "over_budget")]
    exact = await make_ctx(budget_max=Decimal("105.5"), travel_minutes=[None])
    assert await validate_itinerary(d, exact) == []
    assert await validate_itinerary(d, await make_ctx(budget_max=None)) == []


# --- event_date ---------------------------------------------------------------------------


async def test_event_on_another_date_is_flagged() -> None:
    event = await _an_event(FRIDAY)
    ctx = await make_ctx(seen_events={event.event_id: event})
    right = draft(stop(at(FRIDAY, 18), place_id=None, event_id=event.event_id))
    assert await validate_itinerary(right, ctx) == []
    next_day = FRIDAY + dt.timedelta(days=1)
    wrong_ctx = replace(ctx, window_end=at(next_day, 23))
    wrong = draft(stop(at(next_day, 18), place_id=None, event_id=event.event_id))
    assert await codes(wrong, wrong_ctx) == [(0, "event_date")]


# --- outside_window -----------------------------------------------------------------------


async def test_stop_outside_the_window_is_flagged() -> None:
    ctx = await make_ctx()  # 17:00-23:00
    assert await codes(draft(stop(at(FRIDAY, 16))), ctx) == [(0, "outside_window")]
    assert await codes(draft(stop(at(FRIDAY, 22, 30))), ctx) == [(0, "outside_window")]
    assert await validate_itinerary(draft(stop(at(FRIDAY, 17), 360)), ctx) == []


# --- bad_times ----------------------------------------------------------------------------


async def test_end_not_after_start_is_bad_times() -> None:
    ctx = await make_ctx()
    zero = stop(at(FRIDAY, 18), 0)
    backwards = stop(at(FRIDAY, 19), -30)
    assert await codes(draft(zero), ctx) == [(0, "bad_times")]
    assert await codes(draft(backwards), ctx) == [(0, "bad_times")]


async def test_overlapping_stops_are_bad_times() -> None:
    ctx = await make_ctx(travel_minutes=[0])
    d = draft(stop(at(FRIDAY, 18), 90), stop(at(FRIDAY, 19)))
    assert await codes(d, ctx) == [(1, "bad_times")]
    back_to_back = draft(stop(at(FRIDAY, 18)), stop(at(FRIDAY, 19)))
    assert await validate_itinerary(back_to_back, replace(ctx, buffer_minutes=0)) == []


@pytest.mark.parametrize("field", ["place_id", "event_id"])
def test_empty_ids_are_rejected_by_the_schema(field: str) -> None:
    from pydantic import ValidationError

    from app.ai.schemas import PlanOptionStop

    base = {"title": "Dinner", "kind": "food", "evergreen": False}
    PlanOptionStop.model_validate({**base, field: "x"})  # the rest of the stop is valid
    with pytest.raises(ValidationError) as caught:
        PlanOptionStop.model_validate({**base, field: ""})
    assert [err["loc"] for err in caught.value.errors()] == [(field,)]
