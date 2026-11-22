"""The planning service with scripted AI responders: server-side grounding, validation and the
single repair pass, and what is (and is not) persisted."""

import datetime as dt
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.ai.client import AIClient, AIError, FakeAIClient, ToolRunner
from app.ai.schemas import (
    DraftChecklistItem,
    DraftStop,
    ItineraryDraft,
    PlanOption,
    PlanOptions,
    PlanOptionStop,
)
from app.errors import ApiError
from app.models import AIUsage, ChecklistItem, Idea, Itinerary, Stop, TravelLeg
from app.schemas.plan import PlanSessionIn
from app.services import planning
from tests.itinerary_helpers import (
    ALLOWED_VENUE_KEYS,
    TAQUERIA,
    TRATTORIA,
    World,
    fake_providers,
    set_home,
    when,
    world,
)

pytestmark = pytest.mark.db

Responder = Callable[[ToolRunner | None, str, list[dict[str, Any]]], Awaitable[Any]]


def window_of(messages: list[dict[str, Any]]) -> tuple[dt.datetime, dt.datetime]:
    text = messages[0]["content"]
    data = json.loads(text[text.index("{") :])
    return (
        dt.datetime.fromisoformat(data["window_start"]),
        dt.datetime.fromisoformat(data["window_end"]),
    )


async def see(tools: ToolRunner | None, place_id: str) -> None:
    """Have the tools return a place, so it counts as seen."""
    assert tools is not None
    result = await tools.execute("get_place_details", {"place_id": place_id})
    assert "error" not in json.loads(result)


def evergreen_stop(start: dt.datetime, minutes: int = 60, **fields: Any) -> DraftStop:
    return DraftStop(
        title="Cook together",
        evergreen=True,
        starts_at=start,
        ends_at=start + dt.timedelta(minutes=minutes),
        description="Pasta and a movie.",
        **fields,
    )


def draft(stops: list[DraftStop], **fields: Any) -> ItineraryDraft:
    values: dict[str, Any] = {"title": "Our night", "stops": stops, "transport_mode": "drive"}
    values.update(fields)
    return ItineraryDraft(**values)


def opt(stops: list[PlanOptionStop], *, grounded: bool = True) -> PlanOption:
    return PlanOption(
        title="Idea",
        pitch="A date.",
        why_for_them="They will love it.",
        stops=stops,
        grounded=grounded,
    )


class Fixture:
    def __init__(self, w: World, plan_id: uuid.UUID) -> None:
        self.w = w
        self.plan_id = plan_id

    async def call[T](self, ai: AIClient, fn: Callable[..., Awaitable[T]], **kwargs: Any) -> T:
        """Run a planning function in its own session and commit, as a route would."""
        w = self.w
        async with w.app.state.sessionmaker() as session:
            plan = await planning.get_plan(session, w.couple_id, w.owner_id, self.plan_id)
            result = await fn(
                session,
                ai=ai,
                providers=w.providers["p"],
                settings=w.app.state.settings,
                sessionmaker=w.app.state.sessionmaker,
                plan=plan,
                **kwargs,
            )
            await session.commit()
            return result


async def new_plan(w: World, **changes: Any) -> Fixture:
    await set_home(w)
    start = when(days=5, hour=18)
    body = PlanSessionIn(window_start=start, window_end=start + dt.timedelta(hours=4), **changes)
    async with w.app.state.sessionmaker() as session:
        plan = await planning.create_session(
            session, couple_id=w.couple_id, planner_user_id=w.owner_id, body=body
        )
        await session.commit()
        return Fixture(w, plan.id)


async def seed_option(fx: Fixture) -> str:
    """One stored option, straight onto the session (no AI run)."""
    async with fx.w.app.state.sessionmaker() as session:
        plan = await planning.get_plan(session, fx.w.couple_id, fx.w.owner_id, fx.plan_id)
        option_id = str(uuid.uuid4())
        walk = opt([PlanOptionStop(title="Walk", kind="outdoors", evergreen=True)])
        plan.options = [{"id": option_id, **walk.model_dump(mode="json")}]
        await session.commit()
    return option_id


async def build_with(fx: Fixture, responder: Responder) -> Itinerary:
    option_id = await seed_option(fx)
    ai = FakeAIClient({ItineraryDraft: responder})
    return await fx.call(
        ai,
        planning.build,
        requester_user_id=fx.w.owner_id,
        option_ids=[uuid.UUID(option_id)],
        combine_note=None,
    )


async def rows[T](w: World, model: type[T]) -> list[T]:
    async with w.app.state.sessionmaker() as session:
        return list((await session.execute(select(model))).scalars())


# --- Sessions


async def test_create_session_validates_the_window(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        start = when(days=5)
        for end in (start, start - dt.timedelta(hours=1), start + dt.timedelta(days=14, seconds=1)):
            body = PlanSessionIn(window_start=start, window_end=end)
            async with w.app.state.sessionmaker() as session:
                with pytest.raises(ApiError) as caught:
                    await planning.create_session(
                        session, couple_id=w.couple_id, planner_user_id=w.owner_id, body=body
                    )
            assert caught.value.status == 422 and caught.value.code == "invalid_window"


# --- Options


async def test_options_are_grounded_by_the_server_not_the_model(db_app: FastAPI) -> None:
    async def respond(tools: ToolRunner | None, context: str, messages: Any) -> PlanOptions:
        await see(tools, TRATTORIA)
        mixed = opt(
            [
                PlanOptionStop(title="Dinner", kind="food", place_id=TRATTORIA, evergreen=False),
                PlanOptionStop(title="Made up", kind="food", place_id="made-up", evergreen=False),
                PlanOptionStop(title="Fake show", kind="event", event_id="nope", evergreen=False),
            ]
        )
        invented = opt(
            [PlanOptionStop(title="Made up", kind="food", place_id="made-up", evergreen=False)],
            grounded=True,  # the model's claim is never trusted
        )
        night_in = opt(
            [PlanOptionStop(title="Cook", kind="activity", evergreen=True)], grounded=True
        )
        return PlanOptions(options=[mixed, invented, night_in])

    async with world(db_app) as w:
        fx = await new_plan(w)
        degraded = await fx.call(
            FakeAIClient({PlanOptions: respond}), planning.plan_options, more=False
        )
        assert degraded == []
        async with w.app.state.sessionmaker() as session:
            plan = await planning.get_plan(session, w.couple_id, w.owner_id, fx.plan_id)
            stored = plan.options
        mixed, invented, night_in = stored
        assert [o["grounded"] for o in stored] == [True, False, False]
        assert [(s["place_id"], s["event_id"], s["evergreen"]) for s in mixed["stops"]] == [
            (TRATTORIA, None, False),
            (None, None, True),
            (None, None, True),
        ]
        assert invented["stops"][0]["place_id"] is None and invented["stops"][0]["evergreen"]
        assert night_in["stops"][0]["evergreen"] is True
        assert len({o["id"] for o in stored}) == 3 and all(uuid.UUID(o["id"]) for o in stored)

        ideas = await rows(w, Idea)
        assert len(ideas) == 3
        by_title = {i.generation["option_id"]: i for i in ideas}
        assert set(by_title) == {o["id"] for o in stored}
        first = by_title[mixed["id"]]
        assert (first.visibility, first.source, first.grounded) == ("private", "plan", True)
        assert first.place_ids == [TRATTORIA]
        assert first.generation["plan_session_id"] == str(fx.plan_id)
        used = await rows(w, AIUsage)
        assert [(u.feature, u.ok) for u in used] == [("plan_options", True)]


# --- Build


def good_draft(messages: list[dict[str, Any]]) -> ItineraryDraft:
    start, _ = window_of(messages)
    return draft([evergreen_stop(start)])


async def test_the_draft_is_repaired_once_and_the_fix_is_kept(db_app: FastAPI) -> None:
    seen: list[list[dict[str, Any]]] = []

    async def respond(tools: ToolRunner | None, context: str, messages: Any) -> ItineraryDraft:
        seen.append(list(messages))
        start, _ = window_of(messages)
        if len(messages) == 1:  # not repaired yet: a made-up venue
            stop = DraftStop(
                title="Mystery bistro",
                place_id="made-up",
                evergreen=False,
                starts_at=start,
                ends_at=start + dt.timedelta(hours=1),
                description="Dinner.",
            )
            return draft([stop])
        return good_draft(messages)

    async with world(db_app) as w:
        fx = await new_plan(w)
        itinerary = await build_with(fx, respond)
        assert [len(m) for m in seen] == [1, 3]
        assert seen[1][1]["role"] == "assistant" and seen[1][2]["role"] == "user"
        assert "<validation_issues>" in seen[1][2]["content"]
        assert "ungrounded" in seen[1][2]["content"]
        assert itinerary.warnings == []
        used = await rows(w, AIUsage)
        assert [(u.feature, u.ok) for u in used] == [("build", True)]  # one reservation


async def test_an_unseen_but_real_place_is_stripped_and_repaired(db_app: FastAPI) -> None:
    seen: list[list[dict[str, Any]]] = []

    async def respond(tools: ToolRunner | None, context: str, messages: Any) -> ItineraryDraft:
        seen.append(list(messages))
        start, _ = window_of(messages)
        if len(messages) == 1:  # a real venue, but no tool returned it this run
            first = DraftStop(
                title="Dinner",
                place_id=TRATTORIA,
                evergreen=False,
                starts_at=start,
                ends_at=start + dt.timedelta(hours=1),
                description="Pasta.",
            )
            return draft([first])
        await see(tools, TRATTORIA)
        return draft(
            [
                DraftStop(
                    title="Dinner",
                    place_id=TRATTORIA,
                    evergreen=False,
                    starts_at=start,
                    ends_at=start + dt.timedelta(hours=1),
                    description="Pasta.",
                )
            ]
        )

    async with world(db_app) as w:
        fx = await new_plan(w)
        await build_with(fx, respond)
        assert [len(m) for m in seen] == [1, 3]
        assert "ungrounded" in seen[1][2]["content"]
        assert TRATTORIA not in seen[1][1]["content"]  # the repair turn shows it stripped
        (stop,) = await rows(w, Stop)
        assert stop.place_id == TRATTORIA and stop.venue is not None


async def test_tight_travel_between_seen_places_is_flagged(db_app: FastAPI) -> None:
    async def respond(tools: ToolRunner | None, context: str, messages: Any) -> ItineraryDraft:
        await see(tools, TRATTORIA)
        await see(tools, TAQUERIA)
        start, _ = window_of(messages)
        stops = [
            DraftStop(
                title=title,
                place_id=place_id,
                evergreen=False,
                starts_at=start + dt.timedelta(hours=hours),
                ends_at=start + dt.timedelta(hours=hours + 1),
                description="Eat.",
            )
            for title, place_id, hours in (("One", TRATTORIA, 0), ("Two", TAQUERIA, 1))
        ]
        return draft(stops)  # back to back: no room for travel

    async with world(db_app) as w:
        fx = await new_plan(w)
        itinerary = await build_with(fx, respond)
        assert "tight_travel" in {x["code"] for x in itinerary.warnings}


async def test_issues_left_after_the_repair_become_warnings(db_app: FastAPI) -> None:
    calls = 0

    async def respond(tools: ToolRunner | None, context: str, messages: Any) -> ItineraryDraft:
        nonlocal calls
        calls += 1
        start, _ = window_of(messages)
        stop = DraftStop(
            title="Mystery bistro",
            place_id="made-up",
            evergreen=False,
            starts_at=start,
            ends_at=start + dt.timedelta(hours=1),
            description="Dinner.",
        )
        return draft([stop])

    async with world(db_app) as w:
        fx = await new_plan(w)
        itinerary = await build_with(fx, respond)
        assert calls == 2  # one run and exactly one repair
        # "ungrounded" is worded for the model, so it is not stored as a warning.
        assert itinerary.warnings == []
        (stop,) = await rows(w, Stop)
        assert stop.place_id is None and stop.venue is None  # an id no tool returned is dropped


async def test_nothing_from_the_model_text_or_links_is_stored(db_app: FastAPI) -> None:
    async def respond(tools: ToolRunner | None, context: str, messages: Any) -> ItineraryDraft:
        await see(tools, TRATTORIA)
        await see(tools, TAQUERIA)
        start, _ = window_of(messages)
        first = DraftStop(
            title="Dinner",
            place_id=TRATTORIA,
            evergreen=False,
            starts_at=start,
            ends_at=start + dt.timedelta(hours=1),
            description="Pasta.",
            est_cost=40,
            booking_url="https://evil.example/book",
        )
        second = DraftStop(
            title="Tacos",
            place_id=TAQUERIA,
            evergreen=False,
            starts_at=start + dt.timedelta(hours=2),
            ends_at=start + dt.timedelta(hours=3),
            description="Tacos.",
            est_cost=15.5,
        )
        return draft(
            [first, second],
            transport_mode="walk",
            checklist=[
                DraftChecklistItem(
                    text="Book a table",
                    kind="reserve",
                    link="https://evil.example/table",
                    due_at=start - dt.timedelta(days=1),
                )
            ],
        )

    async with world(db_app) as w:
        fx = await new_plan(w, budget_max=100)
        itinerary = await build_with(fx, respond)
        stops = sorted(await rows(w, Stop), key=lambda s: s.position)
        assert [s.place_id for s in stops] == [TRATTORIA, TAQUERIA]
        for stop in stops:
            assert stop.booking_url is None
            assert stop.venue is not None and set(stop.venue) == ALLOWED_VENUE_KEYS
        (item,) = await rows(w, ChecklistItem)
        assert (item.text, item.kind, item.link) == ("Book a table", "reserve", None)
        assert item.due_at is not None
        (leg,) = await rows(w, TravelLeg)
        assert leg.mode == "walk" and leg.duration_minutes > 3  # our own estimate
        assert str(itinerary.est_total) == "55.50" and str(itinerary.budget) == "100.00"
        assert itinerary.status == "draft" and itinerary.planner_user_id == w.owner_id
        assert itinerary.plan_session_id == fx.plan_id
        assert itinerary.starts_at == stops[0].starts_at and itinerary.ends_at == stops[1].ends_at
        assert itinerary.weather is not None


async def test_an_event_stop_keeps_the_event_and_its_coordinates(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        fx = await new_plan(w)
        start = when(days=5, hour=18)
        end = start + dt.timedelta(hours=4)
        event_ids: list[str] = []

        async def respond(tools: ToolRunner | None, context: str, messages: Any) -> ItineraryDraft:
            assert tools is not None
            found = json.loads(
                await tools.execute(
                    "search_events",
                    {"start": start.isoformat(), "end": end.isoformat()},
                )
            )["data"]
            assert found
            event_ids.append(found[0]["event_id"])
            stop = DraftStop(
                title=found[0]["name"],
                event_id=found[0]["event_id"],
                evergreen=False,
                starts_at=start,
                ends_at=start + dt.timedelta(hours=2),
                description="A show.",
            )
            return draft([stop])

        await build_with(fx, respond)
        (stop,) = await rows(w, Stop)
        assert stop.event is not None and stop.event["event_id"] == event_ids[0]
        assert stop.place_id is None
        if stop.venue is not None:
            assert set(stop.venue) == ALLOWED_VENUE_KEYS


async def test_a_failing_build_finalizes_the_usage_and_saves_nothing(db_app: FastAPI) -> None:
    async def respond(tools: ToolRunner | None, context: str, messages: Any) -> ItineraryDraft:
        raise AIError("truncated", "cut off")

    async with world(db_app) as w:
        fx = await new_plan(w)
        with pytest.raises(ApiError) as caught:
            await build_with(fx, respond)
        assert caught.value.status == 502 and caught.value.code == "ai_failed"
        assert [(u.feature, u.ok) for u in await rows(w, AIUsage)] == [("build", False)]
        assert await rows(w, Itinerary) == []
        assert await rows(w, Stop) == []


async def test_plan_all_uses_its_own_feature(db_app: FastAPI) -> None:
    async def respond(tools: ToolRunner | None, context: str, messages: Any) -> ItineraryDraft:
        assert len(messages) == 1 and "chosen_options" not in messages[0]["content"]
        return good_draft(messages)

    async with world(db_app) as w:
        fx = await new_plan(w, steer="surprise me")
        itinerary = await fx.call(
            FakeAIClient({ItineraryDraft: respond}),
            planning.plan_all,
            requester_user_id=w.owner_id,
        )
        assert itinerary.title == "Our night"
        assert [(u.feature, u.ok) for u in await rows(w, AIUsage)] == [("plan_all", True)]


async def test_the_home_area_is_refreshed_before_each_run(db_app: FastAPI) -> None:
    from app.providers.geocoding.fake import FakeGeocoder

    class SpyGeocoder(FakeGeocoder):
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def geocode(self, query: str) -> Any:
            self.queries.append(query)
            return await super().geocode(query)

    async def respond(tools: ToolRunner | None, context: str, messages: Any) -> ItineraryDraft:
        return good_draft(messages)

    async with world(db_app) as w:
        fx = await new_plan(w)
        spy = SpyGeocoder()
        w.providers["p"] = fake_providers(geocoder=spy)
        # Make the stored coordinates 40 days old.
        async with w.app.state.sessionmaker() as session:
            from app.models import Couple

            couple = await session.get(Couple, w.couple_id)
            assert couple is not None
            couple.home_geocoded_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=40)
            await session.commit()
        await fx.call(
            FakeAIClient({ItineraryDraft: respond}),
            planning.plan_all,
            requester_user_id=w.owner_id,
        )
        assert spy.queries == ["Austin"]
