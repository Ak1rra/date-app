"""The DEMO_MODE planning responders: schema-valid, deterministic and grounded only in what the
tools returned."""

import datetime as dt
import json
import uuid
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

import app.ai.fakes as fakes
from app.ai.client import FAKE_RESPONDERS, FakeAIClient
from app.ai.schemas import ItineraryDraft, PlanOptions
from app.ai.tools import ToolExecutor
from app.ai.validate import ValidationContext, validate_itinerary
from app.providers.places.fake import DEMO_CENTER
from app.providers.registry import Providers, build_providers
from app.providers.types import LatLng
from tests.conftest import make_settings

WINDOW_START = dt.datetime(2030, 6, 7, 18, 0, tzinfo=dt.UTC)
WINDOW_END = dt.datetime(2030, 6, 7, 22, 30, tzinfo=dt.UTC)


def executor(prov: Providers | None = None, home: LatLng | None = DEMO_CENTER) -> ToolExecutor:
    return ToolExecutor(
        prov or build_providers(make_settings(demo_mode=True, weather_provider="fake")),
        cast(AsyncSession, None),
        couple_id=uuid.uuid4(),
        requester_user_id=uuid.uuid4(),
        recipient_profile_id=uuid.uuid4(),
        home=home,
        timezone="UTC",
        radius_km=10,
    )


def window_message(start: dt.datetime = WINDOW_START, end: dt.datetime = WINDOW_END) -> list[Any]:
    body = json.dumps({"window_start": start.isoformat(), "window_end": end.isoformat()})
    return [{"role": "user", "content": body}]


def test_responders_are_registered_on_import() -> None:
    assert FAKE_RESPONDERS[PlanOptions] is fakes.fake_plan_options
    assert FAKE_RESPONDERS[ItineraryDraft] is fakes.fake_itinerary


async def test_plan_options_are_valid_distinct_and_grounded_in_seen_places() -> None:
    ex = executor()
    out = await fakes.fake_plan_options(ex, "{}", [])
    assert PlanOptions.model_validate_json(out.model_dump_json()) == out
    assert len({o.title for o in out.options}) == 3
    assert ex.seen_places  # it really searched
    grounded = [s for o in out.options for s in o.stops if not s.evergreen]
    assert grounded
    for option in out.options:
        for s in option.stops:
            assert (s.place_id is None) == s.evergreen
            if s.place_id:
                assert s.place_id in ex.seen_places
        assert option.grounded == all(not s.evergreen for s in option.stops)
    assert any(o.grounded for o in out.options)
    assert any(not o.grounded for o in out.options)  # the stay-in option is evergreen


async def test_plan_options_are_deterministic() -> None:
    first = await fakes.fake_plan_options(executor(), "{}", [])
    second = await fakes.fake_plan_options(executor(), "{}", [])
    assert first == second


async def test_plan_options_without_tools_or_providers_are_all_evergreen() -> None:
    for tools in (None, executor(Providers(None, None, None, None, None))):
        out = await fakes.fake_plan_options(tools, "{}", [])
        assert len(out.options) == 3
        assert all(s.evergreen and s.place_id is None for o in out.options for s in o.stops)
        assert not any(o.grounded for o in out.options)


async def test_itinerary_uses_the_requested_window_and_passes_validation() -> None:
    ex = executor()
    out = await fakes.fake_itinerary(ex, "{}", window_message())
    assert ItineraryDraft.model_validate_json(out.model_dump_json()) == out
    assert out.stops[0].starts_at == WINDOW_START
    assert all(WINDOW_START <= s.starts_at < s.ends_at <= WINDOW_END for s in out.stops)
    for s in out.stops:
        assert s.evergreen == (s.place_id is None)
        if s.place_id:
            assert s.place_id in ex.seen_places

    travel: list[int | None] = []
    for here, there in zip(out.stops, out.stops[1:], strict=False):
        if here.place_id and there.place_id:
            leg = json.loads(
                await ex.execute(
                    "get_travel_time",
                    {
                        "from_place_id": here.place_id,
                        "to_place_id": there.place_id,
                        "mode": "drive",
                    },
                )
            )
            travel.append(leg["data"]["duration_minutes"])
        else:
            travel.append(None)
    ctx = ValidationContext(
        seen_places=ex.seen_places,
        seen_events=ex.seen_events,
        places=None,
        timezone="UTC",
        budget_max=None,
        travel_minutes=travel,
        buffer_minutes=10,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert await validate_itinerary(out, ctx) == []


async def test_itinerary_checklist_kinds_match_the_database_check() -> None:
    out = await fakes.fake_itinerary(executor(), "{}", window_message())
    assert out.checklist  # the dinner stop yields a reservation item
    db_kinds = {"reserve", "buy", "bring", "arrange", "wear", "other"}
    assert {item.kind for item in out.checklist} <= db_kinds


async def test_itinerary_drops_stops_that_do_not_fit_a_short_window() -> None:
    short_end = WINDOW_START + dt.timedelta(hours=2)
    out = await fakes.fake_itinerary(executor(), "{}", window_message(end=short_end))
    assert len(out.stops) == 1
    assert out.stops[0].ends_at <= short_end


async def test_itinerary_falls_back_to_tomorrow_evening_utc() -> None:
    tomorrow = dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)
    for messages in ([], [{"role": "user", "content": "plan something nice"}]):
        out = await fakes.fake_itinerary(None, "{}", messages)
        assert out.stops[0].starts_at == dt.datetime(
            tomorrow.year, tomorrow.month, tomorrow.day, 18, tzinfo=dt.UTC
        )
        assert all(s.ends_at <= out.stops[0].starts_at.replace(hour=22) for s in out.stops)
        assert all(s.evergreen for s in out.stops)


async def test_itinerary_reads_block_content_messages() -> None:
    body = window_message()[0]["content"]
    messages = [{"role": "user", "content": [{"type": "text", "text": f"Plan: {body}"}]}]
    out = await fakes.fake_itinerary(None, "{}", messages)
    assert out.stops[0].starts_at == WINDOW_START


async def test_the_fake_client_runs_the_responders_with_the_real_executor() -> None:
    client = FakeAIClient(FAKE_RESPONDERS)
    ex = executor()
    result = await client.run(
        tier="smart",
        system="s",
        context="{}",
        messages=window_message(),
        tools=ex,
        output_model=ItineraryDraft,
    )
    assert result.tool_calls >= 1
    assert result.degraded == set()
    assert result.output.stops


def test_checklist_kind_enum_equals_the_database_check() -> None:
    import re
    from typing import get_args

    from sqlalchemy import CheckConstraint

    from app.ai.schemas import ChecklistKind
    from app.models import ChecklistItem

    checks = [
        c
        for c in ChecklistItem.__table__.constraints
        if isinstance(c, CheckConstraint) and "kind" in str(c.sqltext)
    ]
    assert len(checks) == 1
    assert set(get_args(ChecklistKind)) == set(re.findall(r"'(\w+)'", str(checks[0].sqltext)))


def test_transport_modes_are_ones_the_database_accepts() -> None:
    import re
    from typing import get_args

    from sqlalchemy import CheckConstraint

    from app.ai.schemas import TransportMode
    from app.ai.tools import TRAVEL_MODES
    from app.models import TravelLeg

    (check,) = [c for c in TravelLeg.__table__.constraints if isinstance(c, CheckConstraint)]
    allowed = set(re.findall(r"'([a-z]+)'", str(check.sqltext)))
    assert set(get_args(TransportMode)) <= allowed
    assert set(TRAVEL_MODES) <= allowed
