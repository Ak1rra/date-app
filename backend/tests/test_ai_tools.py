"""The tool executor: strict specs, each tool against the fake providers, failure handling,
seen-place tracking, and the calendar tool (which must never leak titles)."""

import datetime as dt
import json
import uuid
from dataclasses import replace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import ToolRunner
from app.ai.tools import THIRD_PARTY_NOTE, TOOL_SPECS, ToolExecutor
from app.models import (
    AvailabilityBlock,
    Couple,
    CoupleMember,
    Itinerary,
    PartnerProfile,
    User,
)
from app.providers.places.fake import DEMO_CENTER
from app.providers.registry import Providers, build_providers
from app.providers.types import ProviderUnavailable
from tests.conftest import make_settings

TZ = "America/Chicago"
NEXT_FRIDAY = dt.datetime(2030, 6, 7, 18, 0, tzinfo=dt.UTC)  # a Friday


def providers() -> Providers:
    return build_providers(make_settings(demo_mode=True, weather_provider="fake"))


def executor(
    prov: Providers | None = None,
    session: AsyncSession | None = None,
    *,
    home: Any = DEMO_CENTER,
    couple_id: uuid.UUID | None = None,
    recipient: uuid.UUID | None = None,
) -> ToolExecutor:
    return ToolExecutor(
        prov or providers(),
        cast(AsyncSession, session),  # the provider tools never touch it
        couple_id=couple_id or uuid.uuid4(),
        requester_user_id=uuid.uuid4(),
        recipient_profile_id=recipient or uuid.uuid4(),
        home=home,
        timezone=TZ,
        radius_km=10,
    )


async def call(ex: ToolExecutor, name: str, **args: Any) -> dict[str, Any]:
    result = json.loads(await ex.execute(name, args))
    assert isinstance(result, dict)
    return result


def window() -> dict[str, str]:
    return {
        "start": NEXT_FRIDAY.isoformat(),
        "end": (NEXT_FRIDAY + dt.timedelta(hours=4)).isoformat(),
    }


# --- specs --------------------------------------------------------------------------------


def test_executor_satisfies_the_tool_runner_protocol() -> None:
    runner: ToolRunner = executor()
    assert runner.degraded == set()
    assert runner.specs() == TOOL_SPECS


def test_specs_are_strict_and_in_a_stable_order() -> None:
    names = [s["name"] for s in executor().specs()]
    assert names == [
        "search_places",
        "get_place_details",
        "search_events",
        "get_weather",
        "get_travel_time",
        "get_couple_context",
        "check_calendar",
    ]
    assert executor().specs() == executor().specs()
    for spec in executor().specs():
        assert spec["strict"] is True
        schema = spec["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])
        assert spec["description"]


def test_spec_required_fields() -> None:
    by_name = {s["name"]: s["input_schema"] for s in executor().specs()}
    assert by_name["search_places"]["required"] == ["query"]
    assert by_name["search_places"]["properties"]["max_results"]["maximum"] == 8
    assert by_name["get_travel_time"]["required"] == [
        "from_place_id",
        "to_place_id",
        "mode",
    ]
    assert by_name["get_couple_context"]["properties"] == {}


# --- provider tools -----------------------------------------------------------------------


async def test_search_places_wraps_data_as_third_party_and_tracks_seen() -> None:
    ex = executor()
    out = await call(ex, "search_places", query="dinner", max_results=3)
    assert out["source"] == "places"
    assert out["note"] == THIRD_PARTY_NOTE
    data = out["data"]
    assert 1 <= len(data) <= 3
    assert {p["place_id"] for p in data} == set(ex.seen_places)
    assert all(pid.startswith("demo-place-") for pid in ex.seen_places)
    assert ex.degraded == set()


async def test_search_places_honours_open_at_in_the_couple_timezone() -> None:
    ex = executor()
    # 18:00 UTC Friday is 13:00 in Chicago, when the dinner-only venues are still shut.
    early = await call(ex, "search_places", query="dinner", open_at=NEXT_FRIDAY.isoformat())
    late_at = NEXT_FRIDAY.replace(hour=1, day=8)  # 20:00 Chicago Friday
    late = await call(ex, "search_places", query="dinner", open_at=late_at.isoformat())
    early_ids = {p["place_id"] for p in early["data"]}
    late_ids = {p["place_id"] for p in late["data"]}
    assert "demo-place-sakura-sushi" not in early_ids
    assert "demo-place-sakura-sushi" in late_ids


async def test_get_place_details_and_not_found() -> None:
    ex = executor()
    out = await call(ex, "get_place_details", place_id="demo-place-la-trattoria")
    assert out["data"]["name"] == "La Trattoria"
    assert "demo-place-la-trattoria" in ex.seen_places
    missing = await call(ex, "get_place_details", place_id="nope")
    assert missing == {"error": "not found"}
    assert ex.degraded == set()  # the provider answered; it just has nothing for that id


async def test_search_events_tracks_seen_events() -> None:
    ex = executor()
    out = await call(
        ex,
        "search_events",
        start=NEXT_FRIDAY.isoformat(),
        end=(NEXT_FRIDAY + dt.timedelta(days=3)).isoformat(),
        max_results=5,
    )
    assert out["source"] == "events"
    assert out["data"], "the fake provider has events on every day"
    assert {e["event_id"] for e in out["data"]} == set(ex.seen_events)


async def test_get_weather_returns_a_forecast() -> None:
    out = await call(executor(), "get_weather", **window())
    assert out["source"] == "weather"
    assert out["data"]["risk"] in ("none", "low", "meaningful")
    assert out["data"]["hourly"]


async def test_get_travel_time_resolves_places_and_modes() -> None:
    ex = executor()
    out = await call(
        ex,
        "get_travel_time",
        from_place_id="demo-place-la-trattoria",
        to_place_id="demo-place-riverside-park",
        mode="walk",
    )
    assert out["source"] == "routes"
    assert out["data"]["mode"] == "walk"
    assert out["data"]["duration_minutes"] >= 3
    assert {"demo-place-la-trattoria", "demo-place-riverside-park"} <= set(ex.seen_places)


async def test_get_travel_time_unknown_place_is_an_error_not_a_degradation() -> None:
    ex = executor()
    out = await call(
        ex,
        "get_travel_time",
        from_place_id="nope",
        to_place_id="demo-place-riverside-park",
        mode="drive",
    )
    assert out == {"error": "not found"}
    assert ex.degraded == set()


# --- failure handling ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args", "label"),
    [
        ("search_places", {"query": "dinner"}, "places"),
        ("get_place_details", {"place_id": "demo-place-la-trattoria"}, "places"),
        ("search_events", window(), "events"),
        ("get_weather", window(), "weather"),
        (
            "get_travel_time",
            {"from_place_id": "a", "to_place_id": "b", "mode": "drive"},
            "routes",
        ),
    ],
)
async def test_missing_provider_is_an_error_result_and_degraded(
    tool: str, args: dict[str, Any], label: str
) -> None:
    ex = executor(Providers(None, None, None, None, None))
    assert await call(ex, tool, **args) == {"error": f"{label} unavailable"}
    assert ex.degraded == {label}


class _Down:
    """Every provider call raises ProviderUnavailable."""

    async def search(self, *a: Any, **k: Any) -> Any:
        raise ProviderUnavailable("down", "down")

    details = forecast = travel = search


async def test_provider_unavailable_is_an_error_result_and_degraded() -> None:
    down: Any = _Down()
    prov = replace(providers(), places=down, events=down, weather=down, routes=down)
    ex = executor(prov)
    for tool, args, label in [
        ("search_places", {"query": "dinner"}, "places"),
        ("search_events", window(), "events"),
        ("get_weather", window(), "weather"),
    ]:
        assert await call(ex, tool, **args) == {"error": f"{label} unavailable"}
    assert ex.degraded == {"places", "events", "weather"}
    # A route needs two places first: with places down it reports places.
    out = await call(ex, "get_travel_time", from_place_id="a", to_place_id="b", mode="drive")
    assert out == {"error": "places unavailable"}


async def test_routes_down_after_places_resolve_marks_routes_degraded() -> None:
    down: Any = _Down()
    ex = executor(replace(providers(), routes=down))
    out = await call(
        ex,
        "get_travel_time",
        from_place_id="demo-place-la-trattoria",
        to_place_id="demo-place-riverside-park",
        mode="drive",
    )
    assert out == {"error": "routes unavailable"}
    assert ex.degraded == {"routes"}


async def test_no_home_area_is_an_error_and_not_a_degradation() -> None:
    ex = executor(home=None)
    assert await call(ex, "search_places", query="dinner") == {"error": "home area not set"}
    assert ex.degraded == set()


async def test_unknown_tool_and_bad_input_never_raise() -> None:
    ex = executor()
    assert await call(ex, "launch_missiles") == {"error": "unknown tool"}
    assert await call(ex, "search_places") == {"error": "invalid input"}
    assert await call(ex, "get_weather", start="not a date", end="nope") == {
        "error": "invalid input"
    }
    assert await call(
        ex, "get_travel_time", from_place_id="a", to_place_id="b", mode="teleport"
    ) == {"error": "invalid input"}


# --- database tools -----------------------------------------------------------------------

db = pytest.mark.db


class Fx:
    couple_id: uuid.UUID
    planner: PartnerProfile
    recipient: PartnerProfile
    user_id: uuid.UUID


async def seed(session: AsyncSession) -> Fx:
    fx = Fx()
    couple = Couple(timezone=TZ, home_area_label="Austin", home_lat=30.27, home_lng=-97.74)
    ann, bea = User(email="ann@example.com"), User(email="bea@example.com")
    session.add_all([couple, ann, bea])
    await session.flush()
    fx.couple_id, fx.user_id = couple.id, ann.id
    session.add_all(
        [
            CoupleMember(couple_id=couple.id, user_id=ann.id, role="owner"),
            CoupleMember(couple_id=couple.id, user_id=bea.id, role="partner"),
        ]
    )
    fx.planner = PartnerProfile(
        couple_id=couple.id, slot="owner", subject_user_id=ann.id, display_name="Ann Smith"
    )
    fx.recipient = PartnerProfile(
        couple_id=couple.id, slot="partner", subject_user_id=bea.id, display_name="Bea Jones"
    )
    session.add_all([fx.planner, fx.recipient])
    await session.commit()
    return fx


@db
async def test_check_calendar_reports_busy_times_and_blocks_but_never_titles(
    db_session: AsyncSession,
) -> None:
    fx = await seed(db_session)
    other = Couple(timezone=TZ)
    db_session.add(other)
    await db_session.flush()
    secret = "Secret proposal at the lighthouse"

    def itin(couple_id: uuid.UUID, status: str, hour: int, title: str, **kw: Any) -> Itinerary:
        start = NEXT_FRIDAY.replace(hour=hour)
        return Itinerary(
            couple_id=couple_id,
            planner_user_id=fx.user_id,
            title=title,
            starts_at=start,
            ends_at=start + dt.timedelta(hours=2),
            status=status,
            **kw,
        )

    db_session.add_all(
        [
            itin(fx.couple_id, "scheduled", 18, secret, is_surprise=True),
            itin(fx.couple_id, "draft", 20, "A mere draft"),
            itin(other.id, "scheduled", 18, "Someone else's date"),
            Itinerary(
                couple_id=fx.couple_id,
                planner_user_id=fx.user_id,
                title="Far future",
                starts_at=NEXT_FRIDAY + dt.timedelta(days=30),
                ends_at=NEXT_FRIDAY + dt.timedelta(days=30, hours=2),
                status="scheduled",
            ),
            AvailabilityBlock(
                profile_id=fx.recipient.id, date=dt.date(2030, 6, 8), note="private note"
            ),
            AvailabilityBlock(profile_id=fx.planner.id, date=dt.date(2030, 6, 9)),
            AvailabilityBlock(profile_id=fx.planner.id, date=dt.date(2031, 1, 1)),
        ]
    )
    await db_session.commit()

    ex = executor(session=db_session, couple_id=fx.couple_id, recipient=fx.recipient.id)
    raw = await ex.execute(
        "check_calendar",
        {
            "start": NEXT_FRIDAY.isoformat(),
            "end": (NEXT_FRIDAY + dt.timedelta(days=3)).isoformat(),
        },
    )
    out = json.loads(raw)
    assert out["data"]["busy"] == [
        {
            "starts_at": NEXT_FRIDAY.replace(hour=18).isoformat(),
            "ends_at": NEXT_FRIDAY.replace(hour=20).isoformat(),
        }
    ]
    assert out["data"]["unavailable_dates"] == [
        {"date": "2030-06-08", "who": "recipient"},
        {"date": "2030-06-09", "who": "planner"},
    ]
    for leaked in (secret, "mere draft", "Someone else", "private note", "Far future"):
        assert leaked not in raw


@db
async def test_get_couple_context_returns_the_built_context(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    ex = ToolExecutor(
        providers(),
        db_session,
        couple_id=fx.couple_id,
        requester_user_id=fx.user_id,
        recipient_profile_id=fx.recipient.id,
        home=DEMO_CENTER,
        timezone=TZ,
        radius_km=10,
    )
    out = json.loads(await ex.execute("get_couple_context", {}))
    assert out["data"]["recipient"]["name"] == "Bea"
    assert out["data"]["couple"]["timezone"] == TZ
    assert "Jones" not in json.dumps(out)
