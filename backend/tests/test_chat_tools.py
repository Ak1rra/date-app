"""The chat tool runner: the inner tools plus the three proposal tools, which only record cards."""

import datetime as dt
import json
import uuid
from typing import Any

from app.ai.client import ToolRunner
from app.ai.tools import TOOL_SPECS
from app.services.chat_tools import ChatTools
from tests.test_ai_tools import NEXT_FRIDAY, TZ, executor

NAMES = ["propose_idea", "propose_itinerary_change", "propose_calendar_slot"]


def tools() -> ChatTools:
    return ChatTools(executor())


async def run(chat: ChatTools, name: str, **args: Any) -> dict[str, Any]:
    result = json.loads(await chat.execute(name, args))
    assert isinstance(result, dict)
    return result


def idea_args(**changes: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "title": "Rooftop picnic",
        "pitch": "Sunset and snacks.",
        "category": "outdoors",
        "est_cost": 40,
        "duration_minutes": 120,
        "best_time": "evening",
        "indoor_outdoor": "outdoor",
        "tags": ["cozy"],
    }
    args.update(changes)
    return args


def stop(**changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "title": "Dinner",
        "place_id": None,
        "starts_at": NEXT_FRIDAY.isoformat(),
        "ends_at": (NEXT_FRIDAY + dt.timedelta(hours=2)).isoformat(),
        "notes": None,
    }
    values.update(changes)
    return values


def test_chat_tools_satisfy_the_tool_runner_protocol() -> None:
    runner: ToolRunner = tools()
    assert runner.degraded == set()


def test_specs_append_strict_proposal_specs_after_the_inner_ones() -> None:
    specs = tools().specs()
    assert specs[: len(TOOL_SPECS)] == TOOL_SPECS
    proposals = specs[len(TOOL_SPECS) :]
    assert [s["name"] for s in proposals] == NAMES
    for spec in proposals:
        schema = spec["input_schema"]
        assert spec["strict"] is True and schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])


async def test_a_valid_idea_proposal_is_recorded() -> None:
    chat = tools()
    result = await run(chat, "propose_idea", **idea_args())
    (action,) = chat.actions
    assert result["ok"] is True and result["action_id"] == action["id"]
    assert uuid.UUID(action["id"])
    assert action["type"] == "idea"
    assert action["summary"] == "New idea: Rooftop picnic"
    assert action["payload"]["title"] == "Rooftop picnic" and action["payload"]["tags"] == ["cozy"]
    assert action["applied_at"] is None and action["result"] is None


async def test_an_itinerary_change_proposal_is_recorded_with_a_short_summary() -> None:
    chat = tools()
    itinerary_id = str(uuid.uuid4())
    description = "Swap dinner for tacos. " * 10
    await run(
        chat,
        "propose_itinerary_change",
        itinerary_id=itinerary_id,
        description=description,
        stops=[stop()],
    )
    (action,) = chat.actions
    assert action["type"] == "itinerary_change"
    assert action["summary"] == f"Change the stops: {description.strip()[:80]}"
    assert action["payload"]["itinerary_id"] == itinerary_id
    assert action["payload"]["stops"][0]["title"] == "Dinner"


async def test_a_calendar_slot_summary_uses_the_couples_local_time() -> None:
    chat = tools()
    await run(
        chat,
        "propose_calendar_slot",
        starts_at=NEXT_FRIDAY.isoformat(),
        ends_at=None,
        itinerary_id=None,
        idea_id=str(uuid.uuid4()),
    )
    (action,) = chat.actions
    assert action["type"] == "calendar_slot"
    assert TZ == "America/Chicago"  # 18:00 UTC on 2030-06-07 is 13:00 there
    assert action["summary"] == "Put it on the calendar: Fri Jun 7, 13:00"


async def test_an_invalid_proposal_returns_an_error_string_and_records_nothing() -> None:
    chat = tools()
    bad: list[tuple[str, dict[str, Any]]] = [
        ("propose_idea", idea_args(title="")),
        ("propose_idea", idea_args(best_time="whenever")),
        ("propose_itinerary_change", {"itinerary_id": "nope", "description": "x", "stops": None}),
        (
            "propose_itinerary_change",
            {"itinerary_id": str(uuid.uuid4()), "description": "x", "stops": []},
        ),
        (
            "propose_calendar_slot",
            {
                "starts_at": NEXT_FRIDAY.isoformat(),
                "ends_at": (NEXT_FRIDAY - dt.timedelta(hours=1)).isoformat(),
                "itinerary_id": None,
                "idea_id": None,
            },
        ),
        ("propose_calendar_slot", {"starts_at": "tomorrow"}),
        # unknown properties and string-for-number are rejected, not ignored or coerced
        ("propose_idea", idea_args(surprise=True)),
        ("propose_idea", idea_args(est_cost="25")),
        ("propose_idea", idea_args(duration_minutes="120")),
        (
            "propose_calendar_slot",
            {"starts_at": NEXT_FRIDAY.isoformat(), "ends_at": None, "extra": 1},
        ),
        (
            "propose_itinerary_change",
            {"itinerary_id": str(uuid.uuid4()), "description": "x", "stops": None, "extra": 1},
        ),
        (
            "propose_itinerary_change",
            {"itinerary_id": str(uuid.uuid4()), "description": "x", "stops": [stop(extra=1)]},
        ),
        # a calendar slot is for an itinerary or an idea, never both
        (
            "propose_calendar_slot",
            {
                "starts_at": NEXT_FRIDAY.isoformat(),
                "ends_at": None,
                "itinerary_id": str(uuid.uuid4()),
                "idea_id": str(uuid.uuid4()),
            },
        ),
    ]
    for name, args in bad:
        result = await run(chat, name, **args)
        assert "error" in result and "ok" not in result, (name, args)
    assert chat.actions == []


async def test_other_tools_go_to_the_inner_executor() -> None:
    chat = tools()
    result = await run(chat, "search_places", query="dinner")
    assert "error" not in result and result["data"]
    assert await run(chat, "no_such_tool") == {"error": "unknown tool"}
    assert chat.actions == []
