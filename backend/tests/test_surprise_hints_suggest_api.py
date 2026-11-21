"""AI hint suggestions for a surprise: access, the recorded run, and the spoiler guard."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

import app.ai.fakes  # noqa: F401  (registers the fake responders)
from app.ai.client import FAKE_RESPONDERS, AIError, FakeAIClient, Responder, get_ai
from app.ai.schemas import HintSuggestions, SuggestedHint
from app.errors import ApiError
from app.models import AIUsage, SurpriseHint
from tests.authutil import call
from tests.itinerary_helpers import (
    TRATTORIA,
    DownPlaces,
    World,
    fake_providers,
    seed,
    when,
    world,
)
from tests.test_surprise_hints_api import hints_url, seed_surprise

pytestmark = pytest.mark.db

STOPS: list[dict[str, Any]] = [
    {"title": "Skyline Rooftop Dinner", "description": "Dinner with a view."},
    {"title": "Gelato", "description": "Something sweet."},
]


async def visible_to_partner(w: World) -> uuid.UUID:
    """A date the partner can see but not change (a surprise is never visible to them)."""
    return await seed(
        w,
        stops=STOPS,
        status="scheduled",
        starts_at=when(5),
        announced_at=when(-1),
        is_surprise=False,
    )


def suggest_url(itinerary_id: uuid.UUID) -> str:
    return hints_url(itinerary_id) + "/suggest"


@contextmanager
def fake_ai(app: FastAPI, responders: dict[Any, Responder]) -> Iterator[None]:
    app.dependency_overrides[get_ai] = lambda: FakeAIClient(responders)
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_ai, None)


@contextmanager
def no_ai(app: FastAPI) -> Iterator[None]:
    """The AI dependency as it is with no API key and no demo mode: it raises a 503."""

    def unavailable() -> None:
        raise ApiError(503, "ai_unavailable", "The planner isn't available right now.")

    app.dependency_overrides[get_ai] = unavailable
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_ai, None)


def saying(*texts: str) -> Responder:
    async def respond(tools: Any, context: str, messages: Any) -> HintSuggestions:
        return HintSuggestions(hints=[SuggestedHint(kind="custom", text=t) for t in texts])

    return respond


async def usage_rows(w: World) -> list[AIUsage]:
    async with w.app.state.sessionmaker() as session:
        return list((await session.execute(select(AIUsage))).scalars())


async def hint_rows(w: World) -> list[SurpriseHint]:
    async with w.app.state.sessionmaker() as session:
        return list((await session.execute(select(SurpriseHint))).scalars())


async def test_planner_gets_suggestions_and_the_run_is_recorded(db_app: FastAPI) -> None:
    seen: list[Any] = []

    async def spy(tools: Any, context: str, messages: Any) -> HintSuggestions:
        seen.append((tools, messages))
        return await FAKE_RESPONDERS[HintSuggestions](tools, context, messages)

    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, stops=STOPS)
        with fake_ai(db_app, {HintSuggestions: spy}):
            response = await call(w.owner, "POST", suggest_url(itinerary_id))
        assert response.status_code == 200, response.text
        assert response.json() == {
            "suggestions": [
                {"kind": "dress_code", "text": "Dress for a cozy evening — comfy shoes win."},
                {"kind": "eat", "text": "Come a little hungry."},
                {"kind": "ready_by", "text": "Be ready by the start time with a smile."},
            ]
        }
        assert seen[0][0] is None and "Skyline Rooftop Dinner" in str(seen[0][1])
        assert [(u.feature, u.ok, u.tool_calls) for u in await usage_rows(w)] == [
            ("hints", True, 0)
        ]
        assert await hint_rows(w) == []


async def test_default_fake_ai_answers(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, stops=STOPS)
        with fake_ai(db_app, dict(FAKE_RESPONDERS)):
            response = await call(w.owner, "POST", suggest_url(itinerary_id))
        assert response.status_code == 200, response.text
        assert len(response.json()["suggestions"]) == 3


async def test_partner_gets_403(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await visible_to_partner(w)
        hidden_id = await seed_surprise(w, stops=STOPS)
        with fake_ai(db_app, {HintSuggestions: saying("Wear shoes.")}):
            response = await call(w.partner, "POST", suggest_url(itinerary_id))
            hidden = await call(w.partner, "POST", suggest_url(hidden_id))
        assert response.status_code == 403 and hidden.status_code == 404
        assert await usage_rows(w) == []


async def test_not_a_surprise_is_409(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, stops=STOPS, is_surprise=False, reveal_at=None)
        with fake_ai(db_app, {HintSuggestions: saying("Wear shoes.")}):
            response = await call(w.owner, "POST", suggest_url(itinerary_id))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_a_surprise"


@pytest.mark.parametrize("status", ["cancelled", "completed"])
async def test_closed_itinerary_is_409_not_editable(db_app: FastAPI, status: str) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, stops=STOPS, status=status)
        with fake_ai(db_app, {HintSuggestions: saying("Wear shoes.")}):
            response = await call(w.owner, "POST", suggest_url(itinerary_id))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_editable"


async def test_ai_unavailable_is_503_and_access_errors_still_win(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, stops=STOPS)
        visible_id = await visible_to_partner(w)
        with no_ai(db_app):
            owner = await call(w.owner, "POST", suggest_url(itinerary_id))
            partner = await call(w.partner, "POST", suggest_url(visible_id))
        assert owner.status_code == 503
        assert owner.json()["error"]["code"] == "ai_unavailable"
        assert partner.status_code == 403


async def test_run_failing_as_unavailable_is_503(db_app: FastAPI) -> None:
    async def down(*args: Any) -> Any:
        raise AIError("ai_unavailable", "network")

    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, stops=STOPS)
        with fake_ai(db_app, {HintSuggestions: down}):
            response = await call(w.owner, "POST", suggest_url(itinerary_id))
        assert response.status_code == 503
        assert [(u.feature, u.ok) for u in await usage_rows(w)] == [("hints", False)]


async def test_other_ai_failure_is_502(db_app: FastAPI) -> None:
    async def broken(*args: Any) -> Any:
        raise AIError("ai_invalid_output", "bad json")

    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, stops=STOPS)
        with fake_ai(db_app, {HintSuggestions: broken}):
            response = await call(w.owner, "POST", suggest_url(itinerary_id))
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "ai_failed"


async def test_spoiling_suggestions_are_dropped(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, stops=STOPS)
        responder = saying(
            "Dress up for the SKYLINE ROOFTOP DINNER.",
            "  Come a little hungry.  ",
            "Come a little hungry.",
            "Save room for gelato.",
        )
        with fake_ai(db_app, {HintSuggestions: responder}):
            response = await call(w.owner, "POST", suggest_url(itinerary_id))
        assert response.status_code == 200, response.text
        assert [s["text"] for s in response.json()["suggestions"]] == ["Come a little hungry."]
        assert await hint_rows(w) == []


async def test_all_spoilers_is_502_and_the_run_still_counts_ok(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, stops=STOPS)
        with fake_ai(db_app, {HintSuggestions: saying("Skyline Rooftop Dinner, anyone?")}):
            response = await call(w.owner, "POST", suggest_url(itinerary_id))
        assert response.status_code == 502
        error = response.json()["error"]
        assert error["code"] == "ai_failed"
        assert "spoiler" in str(error)
        assert [(u.feature, u.ok) for u in await usage_rows(w)] == [("hints", True)]
        assert await hint_rows(w) == []


async def test_a_live_place_name_that_differs_from_the_title_is_caught(db_app: FastAPI) -> None:
    stops = [{"title": "Secret dinner", "place_id": TRATTORIA}]
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, stops=stops)
        responder = saying("Dress up for La Trattoria.", "Come a little hungry.")
        with fake_ai(db_app, {HintSuggestions: responder}):
            response = await call(w.owner, "POST", suggest_url(itinerary_id))
        assert response.status_code == 200, response.text
        assert [s["text"] for s in response.json()["suggestions"]] == ["Come a little hungry."]


async def test_case_accent_and_whitespace_variants_are_caught(db_app: FastAPI) -> None:
    stops = [{"title": "Café Lumière"}]
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, stops=stops)
        responder = saying(
            "Meet at CAFÉ   LUMIÈRE soon.",
            "Meet at ＣＡＦÉ lumière soon.",
            "Come a little hungry.",
        )
        with fake_ai(db_app, {HintSuggestions: responder}):
            response = await call(w.owner, "POST", suggest_url(itinerary_id))
        assert response.status_code == 200, response.text
        assert [s["text"] for s in response.json()["suggestions"]] == ["Come a little hungry."]


async def test_the_guard_still_works_when_the_place_lookup_fails(db_app: FastAPI) -> None:
    stops = [{"title": "Secret dinner", "place_id": TRATTORIA}]
    async with world(db_app) as w:
        w.providers["p"] = fake_providers(places=DownPlaces())
        itinerary_id = await seed_surprise(w, stops=stops)
        responder = saying("Ready for the secret dinner?", "Come a little hungry.")
        with fake_ai(db_app, {HintSuggestions: responder}):
            response = await call(w.owner, "POST", suggest_url(itinerary_id))
        assert response.status_code == 200, response.text
        assert [s["text"] for s in response.json()["suggestions"]] == ["Come a little hungry."]


@pytest.mark.parametrize(
    ("title", "leaks"),
    [
        ("Café Lumière", ["Cafe Lumiere", "Café-Lumière", "see you at cafe_lumiere!"]),
        ("La Trattoria", ["La-Trattoria", "LaTrattoria", "la trat toria"]),
        ("Mio", ["Dinner at Mio tonight", "mio!"]),
        ("U2", ["We'll see U2 live", "u-2? no, U2."]),
        ("Øresund Bridge", ["Oresund bridge views", "ØRESUND-BRIDGE"]),
        ("Łódź", ["a trip to Lodz"]),
    ],
)
async def test_punctuation_accent_and_short_name_variants_are_caught(
    db_app: FastAPI, title: str, leaks: list[str]
) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, stops=[{"title": title}])
        with fake_ai(db_app, {HintSuggestions: saying(*leaks, "Come a little hungry.")}):
            response = await call(w.owner, "POST", suggest_url(itinerary_id))
        assert response.status_code == 200, response.text
        assert [s["text"] for s in response.json()["suggestions"]] == ["Come a little hungry."]


async def test_a_short_name_is_not_matched_inside_other_words(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_surprise(w, stops=[{"title": "Mio"}])
        with fake_ai(db_app, {HintSuggestions: saying("Mion is a surprise.", "A premio awaits.")}):
            response = await call(w.owner, "POST", suggest_url(itinerary_id))
        assert response.status_code == 200, response.text
        assert len(response.json()["suggestions"]) == 2


class BrokenPlaces:
    async def search(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise RuntimeError("boom")

    async def details(self, place_id: str) -> Any:
        raise RuntimeError("boom")


async def test_an_unexpected_place_lookup_error_is_skipped_not_a_500(db_app: FastAPI) -> None:
    stops = [{"title": "Secret dinner", "place_id": TRATTORIA}]
    async with world(db_app) as w:
        w.providers["p"] = fake_providers(places=BrokenPlaces())
        itinerary_id = await seed_surprise(w, stops=stops)
        responder = saying("Ready for the secret dinner?", "Come a little hungry.")
        with fake_ai(db_app, {HintSuggestions: responder}):
            response = await call(w.owner, "POST", suggest_url(itinerary_id))
        assert response.status_code == 200, response.text
        assert [s["text"] for s in response.json()["suggestions"]] == ["Come a little hungry."]
