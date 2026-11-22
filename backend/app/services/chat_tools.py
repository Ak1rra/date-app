"""The chat's tool runner: the read-only planning tools plus three proposal tools.

A proposal tool only records an action card for the user; nothing here writes to the database.
The user applies a card later (`services.chat.apply_action`).
"""

import json
import uuid
from typing import Any

from pydantic import BaseModel, ValidationError

from app.ai.tools import ToolExecutor, _error, _schema, _spec
from app.schemas.chat import ProposeCalendarSlot, ProposeIdea, ProposeItineraryChange

_PREFIX = "propose_"
_NOTE = "Shown to the user as a card; nothing is saved until they apply it."
_ID = {"type": "string", "description": "A uuid."}
_TEXT_OR_NULL = {"type": ["string", "null"]}
_DATETIME = {"type": "string", "format": "date-time"}
_DATETIME_OR_NULL = {"type": ["string", "null"], "format": "date-time"}
_ID_OR_NULL = {"type": ["string", "null"], "description": "A uuid, or null."}

_STOP = _schema(
    {
        "title": {"type": "string"},
        "place_id": {
            "type": ["string", "null"],
            "description": "A place_id returned by an earlier tool call, or null.",
        },
        "starts_at": _DATETIME,
        "ends_at": _DATETIME,
        "notes": _TEXT_OR_NULL,
    },
    ["title", "place_id", "starts_at", "ends_at", "notes"],
)

PROPOSAL_SPECS: list[dict[str, Any]] = [
    _spec(
        "propose_idea",
        "Suggest a date idea as a card the user can save with one tap. Nothing is saved until "
        "they do. Use it when you have a concrete idea worth keeping.",
        _schema(
            {
                "title": {"type": "string"},
                "pitch": {"type": "string", "description": "One or two warm sentences."},
                "category": _TEXT_OR_NULL,
                "est_cost": {"type": ["number", "null"], "description": "Total, in dollars."},
                "duration_minutes": {"type": ["integer", "null"]},
                "best_time": {
                    "type": ["string", "null"],
                    "enum": ["day", "evening", "weekend", "any", None],
                },
                "indoor_outdoor": {
                    "type": ["string", "null"],
                    "enum": ["indoor", "outdoor", "either", None],
                },
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            [
                "title",
                "pitch",
                "category",
                "est_cost",
                "duration_minutes",
                "best_time",
                "indoor_outdoor",
                "tags",
            ],
        ),
    ),
    _spec(
        "propose_itinerary_change",
        "Suggest a change to one of the user's existing dates as a card they can apply. "
        "`stops`, when given, is the complete new list of stops and replaces the old one; "
        "leave it null to describe a change in words only.",
        _schema(
            {
                "itinerary_id": _ID,
                "description": {"type": "string", "description": "What changes and why."},
                "stops": {"type": ["array", "null"], "items": _STOP},
            },
            ["itinerary_id", "description", "stops"],
        ),
    ),
    _spec(
        "propose_calendar_slot",
        "Suggest a time for a date as a card the user can apply. Pass `itinerary_id` to "
        "schedule an existing date, or `idea_id` to turn an idea into a date at that time.",
        _schema(
            {
                "starts_at": _DATETIME,
                "ends_at": _DATETIME_OR_NULL,
                "itinerary_id": _ID_OR_NULL,
                "idea_id": _ID_OR_NULL,
            },
            ["starts_at", "ends_at", "itinerary_id", "idea_id"],
        ),
    ),
]

_MODELS: dict[str, type[BaseModel]] = {
    "propose_idea": ProposeIdea,
    "propose_itinerary_change": ProposeItineraryChange,
    "propose_calendar_slot": ProposeCalendarSlot,
}


class ChatTools:
    """The inner executor's tools plus the proposal tools. Valid proposals collect in
    `actions`, ready to be stored on the assistant message."""

    def __init__(self, inner: ToolExecutor) -> None:
        self._inner = inner
        self.actions: list[dict[str, Any]] = []

    @property
    def degraded(self) -> set[str]:
        return self._inner.degraded

    def specs(self) -> list[dict[str, Any]]:
        return [*self._inner.specs(), *(dict(spec) for spec in PROPOSAL_SPECS)]

    async def execute(self, name: str, input: dict[str, Any]) -> str:
        model = _MODELS.get(name)
        if model is None:
            return await self._inner.execute(name, input)
        try:
            proposal = model.model_validate(input)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            return _error(f"invalid input: {where}: {first['msg']}")
        action_id = str(uuid.uuid4())
        self.actions.append(
            {
                "id": action_id,
                "type": name.removeprefix(_PREFIX),
                "summary": self._summary(proposal),
                "payload": proposal.model_dump(mode="json"),
                "applied_at": None,
                "result": None,
            }
        )
        return json.dumps({"ok": True, "action_id": action_id, "note": _NOTE})

    def _summary(self, proposal: BaseModel) -> str:
        if isinstance(proposal, ProposeIdea):
            return f"New idea: {proposal.title}"
        if isinstance(proposal, ProposeItineraryChange):
            return f"Change the stops: {proposal.description[:80]}"
        assert isinstance(proposal, ProposeCalendarSlot)
        local = proposal.starts_at.astimezone(self._inner.tz)
        return f"Put it on the calendar: {local:%a %b} {local.day}, {local:%H:%M}"
