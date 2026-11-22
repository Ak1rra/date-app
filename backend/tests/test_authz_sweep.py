"""An authorization sweep generated from the app's own route table (M8), so it cannot go stale.

Every route under /api/v1 is either PUBLIC (listed below, with the reason it needs no session)
or listed in BODIES (the minimal valid request for it). A route that is in neither fails
`test_every_route_is_classified` until the developer classifies it. Then, for every route in BODIES:

* with no session it is a 401 `unauthenticated` (the WebSocket is refused with 4401), and
* if it has path parameters, another couple's ids are a 404 (never 403, 200 or 422) and
  leave that couple's data untouched. Each path parameter NAME must be in PARAM_NAMES and have an
  object in the `other_couple` fixture, so a new kind of id cannot slip past the check.

A case that fails because of a real bug is marked xfail(strict=True) in BUGS_401 / BUGS_404 with
the reason, so the suite stays green and the gap is written down.
"""

import datetime as dt
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from httpx import AsyncClient
from starlette.routing import WebSocketRoute

from app.main import create_app
from app.models import (
    ChatMessage,
    ChecklistItem,
    Itinerary,
    Notification,
    ProfileSuggestion,
    Stop,
    SurpriseHint,
)
from tests.authutil import browser, call, profile_ids, sign_in
from tests.conftest import make_settings
from tests.test_realtime import Socket

pytestmark = pytest.mark.db

API = "/api/v1"
WEBSOCKET = "WEBSOCKET"
GHOST = "00000000-0000-4000-8000-000000000001"  # a literal uuid for bodies that name an id
WHEN = "2031-01-01T18:00:00Z"
UNTIL = "2031-01-01T20:00:00Z"

Route = tuple[str, str]  # (method, path below /api/v1); the WebSocket's method is WEBSOCKET


def _route_table() -> list[Route]:
    """Every route under /api/v1 of a freshly built app, one entry per method."""
    routes: set[Route] = set()
    # FastAPI keeps included routers nested; iter_route_contexts flattens them with full paths.
    for context in iter_route_contexts(create_app(make_settings()).routes):
        path = context.path or ""
        if not path.startswith(API + "/"):
            continue
        path = path.removeprefix(API)
        if isinstance(context.original_route, APIRoute):
            routes.update((m, path) for m in (context.methods or set()) - {"HEAD", "OPTIONS"})
        elif isinstance(context.original_route, WebSocketRoute):
            routes.add((WEBSOCKET, path))
    return sorted(routes)


ROUTES = _route_table()

# The routes that really work without a session.
PUBLIC: dict[Route, str] = {
    ("POST", "/auth/magic-link"): "asks for a sign-in email, before there is a session",
    ("POST", "/auth/verify"): "trades a magic-link token for a session",
    ("POST", "/auth/choose-profile"): "no-login mode: picks Husband or Wife (404 unless NO_LOGIN)",
    ("GET", "/auth/session"): "reports 'not signed in' instead of failing, for the app shell",
    ("GET", "/health"): "liveness probe for the load balancer and compose",
    ("GET", "/meta"): "public app metadata (sign-in mode, demo flag) shown before sign-in",
    ("GET", "/dev/outbox"): "dev sign-in links; 404 unless DEV_OUTBOX is set",
}

# Every other route, with the smallest request that validates. None means "no body".
BODIES: dict[Route, Any] = {
    ("POST", "/auth/logout"): None,
    ("POST", "/auth/logout-all"): None,
    ("GET", "/calendar"): None,
    ("POST", "/calendar/suggest-times"): {"duration_minutes": 60},
    ("GET", "/chat/threads"): None,
    ("GET", "/chat/threads/{thread_id}/messages"): None,
    ("POST", "/chat/threads/{thread_id}/messages"): {"content": "Hijacked"},
    ("POST", "/chat/actions/{action_id}/apply"): None,
    ("GET", "/couple"): None,
    ("PATCH", "/couple"): {"name": "Hijacked"},
    ("PUT", "/couple/home-area"): {"query": "Austin"},
    ("GET", "/couple/constraints"): None,
    ("PUT", "/couple/constraints"): {},
    ("GET", "/couple/important-dates"): None,
    ("POST", "/couple/important-dates"): {"name": "x", "date": "2031-01-01", "kind": "custom"},
    ("PATCH", "/couple/important-dates/{date_id}"): {"name": "Hijacked"},
    ("DELETE", "/couple/important-dates/{date_id}"): None,
    ("POST", "/couple/invites"): None,
    ("GET", "/couple/invites/current"): None,
    ("DELETE", "/couple/invites/current"): None,
    ("POST", "/couple/join"): {"short_code": "ABCDEFGH"},
    ("POST", "/couple/leave"): None,
    ("PUT", "/itineraries/{itinerary_id}/rating"): {"stars": 1, "would_again": False},
    ("GET", "/itineraries/{itinerary_id}/ratings"): None,
    ("PUT", "/itineraries/{itinerary_id}/planner-read"): {"enjoyment": 1},
    ("GET", "/past-dates"): None,
    ("GET", "/ideas"): None,
    ("POST", "/ideas"): {"title": "x"},
    ("POST", "/ideas/generate"): {"mode": "plan"},
    ("GET", "/ideas/lists"): None,
    ("PATCH", "/ideas/{idea_id}"): {"title": "Hijacked"},
    ("DELETE", "/ideas/{idea_id}"): None,
    ("POST", "/ideas/{idea_id}/share"): None,
    ("GET", "/deck"): None,
    ("PUT", "/ideas/{idea_id}/vote"): {"value": "yes"},
    ("POST", "/deck/undo"): None,
    ("POST", "/ideas/{idea_id}/itinerary"): {},
    ("GET", "/itineraries"): None,
    ("GET", "/itineraries/{itinerary_id}"): None,
    ("PATCH", "/itineraries/{itinerary_id}"): {"title": "Hijacked"},
    ("PUT", "/itineraries/{itinerary_id}/stops"): {
        "stops": [{"title": "Hijacked", "starts_at": WHEN, "ends_at": UNTIL}]
    },
    ("POST", "/itineraries/{itinerary_id}/checklist"): {"text": "Hijacked", "kind": "other"},
    ("PATCH", "/itineraries/{itinerary_id}/checklist/{item_id}"): {"text": "Hijacked"},
    ("DELETE", "/itineraries/{itinerary_id}/checklist/{item_id}"): None,
    ("POST", "/itineraries/{itinerary_id}/schedule"): {"starts_at": WHEN, "mode": "announce"},
    ("POST", "/itineraries/{itinerary_id}/cancel"): None,
    ("POST", "/itineraries/{itinerary_id}/react"): {"kind": "heart"},
    ("POST", "/itineraries/{itinerary_id}/stops/{stop_id}/regenerate"): None,
    ("POST", "/itineraries/{itinerary_id}/polish-invitation"): {"draft": "Hijacked"},
    ("GET", "/itineraries/{itinerary_id}/hints"): None,
    ("POST", "/itineraries/{itinerary_id}/hints/suggest"): None,
    ("POST", "/itineraries/{itinerary_id}/hints"): {"kind": "custom", "text": "Hijacked"},
    ("PATCH", "/itineraries/{itinerary_id}/hints/{hint_id}"): {"text": "Hijacked"},
    ("DELETE", "/itineraries/{itinerary_id}/hints/{hint_id}"): None,
    ("PUT", "/itineraries/{itinerary_id}/reveal-at"): {"reveal_at": None},
    ("POST", "/itineraries/{itinerary_id}/reveal"): None,
    ("GET", "/surprises"): None,
    ("GET", "/surprises/{itinerary_id}"): None,
    ("POST", "/itineraries/{itinerary_id}/template"): {"name": "Hijacked"},
    ("GET", "/templates"): None,
    ("DELETE", "/templates/{template_id}"): None,
    ("POST", "/templates/{template_id}/replan"): {"starts_at": WHEN},
    ("GET", "/me"): None,
    ("PATCH", "/me"): {"display_name": "Hijacked"},
    ("GET", "/me/export"): None,
    ("DELETE", "/me"): None,
    ("GET", "/notes"): None,
    ("POST", "/notes"): {"about_profile_id": GHOST, "kind": "note", "text": "x"},
    ("PATCH", "/notes/{note_id}"): {"text": "Hijacked"},
    ("DELETE", "/notes/{note_id}"): None,
    ("GET", "/notifications"): None,
    ("GET", "/notifications/unread-count"): None,
    ("POST", "/notifications/read-all"): None,
    ("POST", "/notifications/{notification_id}/read"): None,
    ("GET", "/notifications/push-config"): None,
    ("GET", "/me/notification-settings"): None,
    ("PUT", "/me/notification-settings"): {},
    ("POST", "/me/push-subscriptions"): {
        "endpoint": "https://push.example.com/x",
        "keys": {"p256dh": "k", "auth": "a"},
    },
    ("DELETE", "/me/push-subscriptions"): None,
    ("GET", "/places/search"): None,
    ("POST", "/plan/sessions"): {"window_start": WHEN, "window_end": UNTIL},
    ("GET", "/plan/sessions"): None,
    ("GET", "/plan/sessions/{plan_id}"): None,
    ("POST", "/plan/sessions/{plan_id}/options"): None,
    ("POST", "/plan/sessions/{plan_id}/build"): {"option_ids": [GHOST]},
    ("POST", "/plan/sessions/{plan_id}/plan-all"): None,
    ("GET", "/profiles"): None,
    ("GET", "/profiles/{profile_id}"): None,
    ("PUT", "/profiles/{profile_id}"): {"loves": "Hijacked"},
    ("POST", "/profiles/{profile_id}/confirm"): None,
    ("POST", "/profiles/{profile_id}/suggestions"): {"changes": {"loves": "Hijacked"}},
    ("GET", "/profiles/{profile_id}/suggestions"): None,
    ("POST", "/profiles/{profile_id}/suggestions/{suggestion_id}/accept"): None,
    ("POST", "/profiles/{profile_id}/suggestions/{suggestion_id}/decline"): None,
    ("GET", "/profiles/{profile_id}/availability"): None,
    ("PUT", "/profiles/{profile_id}/availability"): {"windows": [], "blocks": []},
    (WEBSOCKET, "/ws"): None,
}

# Every kind of id that appears in a path. Each one needs an object of Bob's in `other_couple`.
PARAM_NAMES = frozenset(
    {
        "action_id",  # a chat action card
        "date_id",  # an important date
        "hint_id",  # a surprise hint
        "idea_id",
        "item_id",  # a checklist item
        "itinerary_id",
        "note_id",
        "notification_id",
        "plan_id",  # a plan session
        "profile_id",
        "stop_id",
        "suggestion_id",  # a profile suggestion
        "template_id",
        "thread_id",  # a chat thread
    }
)

# Real bugs the sweep found: (method, path) -> what happens. Each is a strict xfail, so fixing
# the route turns the case into an XPASS failure until the entry is deleted.
BUGS_401: dict[Route, str] = {}
BUGS_404: dict[Route, str] = {}


def _param_names(path: str) -> list[str]:
    return re.findall(r"\{(\w+)\}", path)


def _cases(routes: list[Route], bugs: dict[Route, str]) -> list[Any]:
    return [
        pytest.param(
            method,
            path,
            id=f"{method} {path}",
            marks=pytest.mark.xfail(strict=True, reason=bugs[(method, path)])
            if (method, path) in bugs
            else (),
        )
        for method, path in routes
    ]


PROTECTED = [route for route in ROUTES if route not in PUBLIC]
WITH_PARAMS = [(m, p) for m, p in PROTECTED if m != WEBSOCKET and _param_names(p)]


def test_every_route_is_classified() -> None:
    table = set(ROUTES)
    unclassified = sorted(table - set(PUBLIC) - set(BODIES))
    assert not unclassified, (
        "These routes are neither PUBLIC nor in BODIES. Classify each one in "
        f"tests/test_authz_sweep.py: PUBLIC if it works without a session (with the reason), "
        f"otherwise BODIES with a minimal valid body (and PARAM_NAMES for new ids): {unclassified}"
    )
    stale = sorted((set(PUBLIC) | set(BODIES)) - table)
    assert not stale, f"These entries match no route any more; delete them: {stale}"
    both = sorted(set(PUBLIC) & set(BODIES))
    assert not both, f"A route cannot be both PUBLIC and protected: {both}"
    unknown = sorted({n for _, p in WITH_PARAMS for n in _param_names(p)} - PARAM_NAMES)
    assert not unknown, (
        f"Path parameters {unknown} are not in PARAM_NAMES. Add them there and seed Bob's "
        "object of that kind in the `other_couple` fixture."
    )


@pytest.mark.parametrize(("method", "template"), _cases(PROTECTED, BUGS_401))
async def test_every_protected_route_is_401_without_a_session(
    db_app: FastAPI, db_client: AsyncClient, method: str, template: str
) -> None:
    if method == WEBSOCKET:
        assert await Socket(db_app).open() == 4401
        return
    path = API + re.sub(r"\{\w+\}", lambda _: str(uuid.uuid4()), template)
    response = await call(db_client, method, path, BODIES[(method, template)])
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "unauthenticated"


@dataclass
class OtherCouple:
    """Ann's browser, Bob's browser, and the id of each kind of object in Bob's couple."""

    ann: AsyncClient
    bob: AsyncClient
    ids: dict[str, str]

    async def snapshot(self) -> dict[str, Any]:
        """Everything of Bob's we can read back, as Bob sees it."""
        ids = self.ids
        paths = [
            f"/itineraries/{ids['itinerary_id']}",
            f"/itineraries/{ids['itinerary_id']}/hints",
            f"/itineraries/{ids['itinerary_id']}/ratings",
            "/ideas",
            f"/plan/sessions/{ids['plan_id']}",
            "/templates",
            "/notifications",
            f"/chat/threads/{ids['thread_id']}/messages",
            "/notes",
            "/couple",
            "/couple/important-dates",
            f"/profiles/{ids['profile_id']}",
            f"/profiles/{ids['profile_id']}/suggestions",
            f"/profiles/{ids['profile_id']}/availability",
            "/me",
        ]
        out: dict[str, Any] = {}
        for path in paths:
            response = await self.bob.get(API + path)
            assert response.status_code == 200, f"{path}: {response.text}"
            out[path] = response.json()
        return out


@pytest.fixture
async def other_couple(db_app: FastAPI) -> AsyncIterator[OtherCouple]:
    """Ann in one couple; Bob in another, with one object of every kind that has an id in a path."""
    async with browser(db_app) as ann, browser(db_app) as bob:
        await sign_in(db_app, ann, "ann@example.com")
        await sign_in(db_app, bob, "bob@example.com")
        me = (await bob.get(f"{API}/me")).json()
        bob_id, couple_id = uuid.UUID(me["id"]), uuid.UUID(me["couple_id"])
        profile_id = (await profile_ids(bob))["partner"]

        async def create(path: str, body: dict[str, Any]) -> str:
            response = await call(bob, "POST", API + path, body)
            assert response.status_code == 201, f"{path}: {response.text}"
            return str(response.json()["id"])

        idea = await create("/ideas", {"title": "Bob's idea"})
        date = await create(
            "/couple/important-dates", {"name": "Bob's", "date": "2031-01-01", "kind": "custom"}
        )
        note = await create(
            "/notes", {"about_profile_id": profile_id, "kind": "note", "text": "Bob's secret"}
        )
        plan = await create("/plan/sessions", {"window_start": WHEN, "window_end": UNTIL})
        thread = {t["scope"]: t["id"] for t in (await bob.get(f"{API}/chat/threads")).json()}[
            "private"
        ]
        start = dt.datetime(2031, 1, 1, 18, tzinfo=dt.UTC)
        action = str(uuid.uuid4())
        async with db_app.state.sessionmaker() as session:
            itinerary = Itinerary(
                couple_id=couple_id,
                planner_user_id=bob_id,
                title="Bob's date",
                status="draft",
                is_surprise=True,  # so the hint routes reach their ownership check too
            )
            session.add(itinerary)
            await session.flush()
            stop = Stop(
                itinerary_id=itinerary.id,
                position=0,
                title="Bob's tacos",
                starts_at=start,
                ends_at=start + dt.timedelta(hours=1),
            )
            item = ChecklistItem(
                itinerary_id=itinerary.id, position=0, text="Bob's booking", kind="reserve"
            )
            hint = SurpriseHint(
                itinerary_id=itinerary.id, kind="custom", text="Bob's hint", release_at=start
            )
            notification = Notification(
                user_id=bob_id, couple_id=couple_id, kind="match", title="Bob's", body="Bob's"
            )
            suggestion = ProfileSuggestion(
                profile_id=uuid.UUID(profile_id), author_user_id=bob_id, changes={"loves": "x"}
            )
            session.add_all(
                [
                    stop,
                    item,
                    hint,
                    notification,
                    suggestion,
                    ChatMessage(
                        thread_id=uuid.UUID(thread),
                        role="assistant",
                        content="Bob's card",
                        actions=[
                            {
                                "id": action,
                                "type": "idea",
                                "summary": "Bob's card",
                                "payload": {"title": "Bob's"},
                                "applied_at": None,
                                "result": None,
                            }
                        ],
                    ),
                ]
            )
            await session.flush()
            ids = {
                "action_id": action,
                "date_id": date,
                "hint_id": str(hint.id),
                "idea_id": idea,
                "item_id": str(item.id),
                "itinerary_id": str(itinerary.id),
                "note_id": note,
                "notification_id": str(notification.id),
                "plan_id": plan,
                "profile_id": profile_id,
                "stop_id": str(stop.id),
                "suggestion_id": str(suggestion.id),
                "thread_id": thread,
            }
            await session.commit()
        ids["template_id"] = await create(
            f"/itineraries/{ids['itinerary_id']}/template", {"name": "Bob's template"}
        )
        assert set(ids) == PARAM_NAMES, "seed one object of each kind in PARAM_NAMES"
        yield OtherCouple(ann, bob, ids)


@pytest.mark.parametrize(("method", "template"), _cases(WITH_PARAMS, BUGS_404))
async def test_another_couples_ids_are_404(
    other_couple: OtherCouple, method: str, template: str
) -> None:
    missing = [n for n in _param_names(template) if n not in other_couple.ids]
    if missing:
        pytest.fail(f"{missing} are not in PARAM_NAMES / the other_couple fixture: seed them")
    before = await other_couple.snapshot()
    path = API + template.format(**other_couple.ids)
    response = await call(other_couple.ann, method, path, BODIES[(method, template)])
    assert response.status_code == 404, f"{response.status_code}: {response.text}"
    assert await other_couple.snapshot() == before
