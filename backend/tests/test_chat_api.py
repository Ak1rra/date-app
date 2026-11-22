"""Chat threads, message history and applying proposed actions, with the fake AI and providers."""

import asyncio
import datetime as dt
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

import app.ai.fakes  # noqa: F401  (registers the fake responders)
from app.models import ChatMessage, ChatThread, Idea, Itinerary, Stop
from tests.authutil import call, sign_in
from tests.itinerary_helpers import World, seed, set_home, when, world

pytestmark = pytest.mark.db

THREADS = "/api/v1/chat/threads"
OWNER = "ann@example.com"


def messages_path(thread_id: str | uuid.UUID) -> str:
    return f"{THREADS}/{thread_id}/messages"


def apply_path(action_id: str | uuid.UUID) -> str:
    return f"/api/v1/chat/actions/{action_id}/apply"


async def threads_of(client: AsyncClient) -> dict[str, str]:
    """scope -> thread id for the caller."""
    response = await client.get(THREADS)
    assert response.status_code == 200, response.text
    return {t["scope"]: t["id"] for t in response.json()}


async def add_message(
    w: World,
    thread_id: str,
    content: str = "hello",
    *,
    role: str = "user",
    author: uuid.UUID | None = None,
    actions: list[dict[str, Any]] | None = None,
    hidden_for: uuid.UUID | None = None,
    at: dt.datetime | None = None,
) -> uuid.UUID:
    async with w.app.state.sessionmaker() as session:
        message = ChatMessage(
            thread_id=uuid.UUID(thread_id),
            role=role,
            author_user_id=author,
            content=content,
            actions=actions or [],
            hidden_for_user_id=hidden_for,
        )
        if at is not None:
            message.created_at = at
        session.add(message)
        await session.commit()
        return message.id


def action(kind: str, payload: dict[str, Any], summary: str = "A card") -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "type": kind,
        "summary": summary,
        "payload": payload,
        "applied_at": None,
        "result": None,
    }


def idea_payload(**changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "title": "Rooftop picnic",
        "pitch": "Sunset and snacks.",
        "tags": ["cozy"],
        "category": None,
        "est_cost": None,
        "duration_minutes": None,
        "best_time": None,
        "indoor_outdoor": None,
    }
    values.update(changes)
    return values


def stop_payload(hour: int = 18) -> dict[str, Any]:
    return {
        "title": "Tacos",
        "place_id": None,
        "starts_at": when(days=5, hour=hour).isoformat(),
        "ends_at": when(days=5, hour=hour + 1).isoformat(),
        "notes": None,
    }


async def stored_actions(w: World, message_id: uuid.UUID) -> list[dict[str, Any]]:
    async with w.app.state.sessionmaker() as session:
        message = (
            await session.execute(select(ChatMessage).where(ChatMessage.id == message_id))
        ).scalar_one()
        return list(message.actions)


# --- threads ------------------------------------------------------------------------------


async def test_every_route_needs_a_signed_in_user(db_client: AsyncClient) -> None:
    assert (await db_client.get(THREADS)).status_code == 401
    assert (await db_client.get(messages_path(uuid.uuid4()))).status_code == 401
    assert (await call(db_client, "POST", apply_path(uuid.uuid4()))).status_code == 401


async def test_threads_are_created_lazily_and_the_shared_one_waits_for_a_partner(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    await sign_in(db_app, db_client, OWNER)
    async with db_app.state.sessionmaker() as session:
        assert (await session.execute(select(ChatThread))).first() is None
    response = await db_client.get(THREADS)
    (mine,) = response.json()
    assert mine["scope"] == "private" and mine["title"] is None and mine["updated_at"]
    assert (await db_client.get(THREADS)).json() == [mine]  # reading again creates nothing
    async with db_app.state.sessionmaker() as session:
        assert len((await session.execute(select(ChatThread))).all()) == 1


async def test_a_couple_gets_a_private_thread_each_and_one_shared_thread(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        owner, partner = await threads_of(w.owner), await threads_of(w.partner)
        assert set(owner) == set(partner) == {"private", "shared"}
        assert owner["shared"] == partner["shared"]
        assert owner["private"] != partner["private"]
        assert [t["scope"] for t in (await w.owner.get(THREADS)).json()] == ["private", "shared"]


async def test_concurrent_first_use_does_not_fail_or_duplicate(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        responses = await asyncio.gather(
            w.owner.get(THREADS), w.owner.get(THREADS), w.partner.get(THREADS)
        )
        assert [r.status_code for r in responses] == [200, 200, 200]
        async with w.app.state.sessionmaker() as session:
            rows = (await session.execute(select(ChatThread))).scalars().all()
        assert sorted(t.scope for t in rows) == ["private", "private", "shared"]
        shared = {t["id"] for r in responses for t in r.json() if t["scope"] == "shared"}
        assert len(shared) == 1


async def test_another_members_private_thread_is_a_404(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        mine, theirs = await threads_of(w.owner), await threads_of(w.partner)
        assert (await w.owner.get(messages_path(mine["private"]))).status_code == 200
        assert (await w.owner.get(messages_path(theirs["private"]))).status_code == 404
        assert (await w.partner.get(messages_path(mine["private"]))).status_code == 404
        assert (await w.partner.get(messages_path(mine["shared"]))).status_code == 200
        assert (await w.owner.get(messages_path(uuid.uuid4()))).status_code == 404


# --- messages -----------------------------------------------------------------------------


async def test_messages_page_with_before_and_has_more(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        thread = (await threads_of(w.owner))["private"]
        start = when(days=-1)
        ids = [
            await add_message(
                w, thread, f"m{i}", author=w.owner_id, at=start + dt.timedelta(minutes=i)
            )
            for i in range(5)
        ]
        first = (await w.owner.get(messages_path(thread), params={"limit": 2})).json()
        assert [m["content"] for m in first["messages"]] == ["m3", "m4"]
        assert first["has_more"] is True
        before = first["messages"][0]["id"]
        assert before == str(ids[3])
        second = (
            await w.owner.get(messages_path(thread), params={"limit": 2, "before": before})
        ).json()
        assert [m["content"] for m in second["messages"]] == ["m1", "m2"]
        assert second["has_more"] is True
        last = (
            await w.owner.get(
                messages_path(thread), params={"limit": 2, "before": second["messages"][0]["id"]}
            )
        ).json()
        assert [m["content"] for m in last["messages"]] == ["m0"] and last["has_more"] is False
        everything = (await w.owner.get(messages_path(thread))).json()
        assert [m["content"] for m in everything["messages"]] == [f"m{i}" for i in range(5)]
        assert everything["has_more"] is False


async def test_message_paging_validates_its_parameters(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        thread = (await threads_of(w.owner))["private"]
        for params in ({"limit": 0}, {"limit": 101}, {"before": "nope"}):
            assert (await w.owner.get(messages_path(thread), params=params)).status_code == 422
        unknown = {"before": str(uuid.uuid4())}
        assert (await w.owner.get(messages_path(thread), params=unknown)).status_code == 404


async def test_hidden_messages_are_omitted_for_the_hidden_user_only(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        thread = (await threads_of(w.owner))["shared"]
        start = when(days=-1)
        await add_message(w, thread, "for both", author=w.owner_id, at=start)
        await add_message(
            w,
            thread,
            "a surprise",
            role="assistant",
            hidden_for=w.partner_id,
            at=start + dt.timedelta(minutes=1),
        )
        mine = (await w.owner.get(messages_path(thread))).json()["messages"]
        theirs = (await w.partner.get(messages_path(thread))).json()["messages"]
        assert [m["content"] for m in mine] == ["for both", "a surprise"]
        assert [m["content"] for m in theirs] == ["for both"]


async def test_message_shape_has_attribution_and_actions(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        thread = (await threads_of(w.owner))["shared"]
        card = action("idea", idea_payload(), "New idea: Rooftop picnic")
        start = when(days=-1)
        await add_message(w, thread, "hi", author=w.owner_id, at=start)
        await add_message(
            w, thread, "try this", role="assistant", actions=[card], at=start + dt.timedelta(1)
        )
        sent, reply = (await w.owner.get(messages_path(thread))).json()["messages"]
        assert sent["role"] == "user" and sent["author_is_me"] is True and sent["actions"] == []
        assert sent["author_user_id"] == str(w.owner_id)
        assert reply["role"] == "assistant" and reply["author_user_id"] is None
        assert reply["author_is_me"] is False
        (out,) = reply["actions"]
        assert out == {
            "id": card["id"],
            "type": "idea",
            "summary": "New idea: Rooftop picnic",
            "payload": card["payload"],
            "applied_at": None,
            "result": None,
        }
        partner_view = (await w.partner.get(messages_path(thread))).json()["messages"]
        assert partner_view[0]["author_is_me"] is False


# --- apply --------------------------------------------------------------------------------


async def test_applying_an_idea_in_a_private_thread_makes_a_private_idea_once(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        thread = (await threads_of(w.owner))["private"]
        card = action("idea", idea_payload())
        message_id = await add_message(w, thread, "idea", role="assistant", actions=[card])
        response = await call(w.owner, "POST", apply_path(card["id"]))
        assert response.status_code == 200, response.text
        out = response.json()
        assert out["idea"]["title"] == "Rooftop picnic" and out["idea"]["visibility"] == "private"
        assert out["idea"]["source"] == "chat" and out["itinerary"] is None
        assert out["action"]["applied_at"] is not None
        assert out["action"]["result"] == {"idea_id": out["idea"]["id"]}
        assert (await stored_actions(w, message_id))[0]["result"] == out["action"]["result"]

        again = await call(w.owner, "POST", apply_path(card["id"]))
        assert again.status_code == 200, again.text
        assert again.json()["action"] == out["action"]
        assert again.json()["idea"] is None and again.json()["itinerary"] is None
        async with w.app.state.sessionmaker() as session:
            assert len((await session.execute(select(Idea))).all()) == 1


async def test_applying_an_idea_in_the_shared_thread_shares_it_for_either_member(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        thread = (await threads_of(w.owner))["shared"]
        card = action("idea", idea_payload(title="Together idea"))
        await add_message(w, thread, "idea", role="assistant", actions=[card])
        response = await call(w.partner, "POST", apply_path(card["id"]))
        assert response.status_code == 200, response.text
        assert response.json()["idea"]["visibility"] == "shared"
        shared = (await w.owner.get("/api/v1/ideas", params={"visibility": "shared"})).json()
        assert [i["title"] for i in shared] == ["Together idea"]


async def test_applying_an_idea_does_not_need_the_ai(
    db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.chat.get_ai_client", lambda settings: None)
    async with world(db_app) as w:
        thread = (await threads_of(w.owner))["private"]
        card = action("idea", idea_payload())
        await add_message(w, thread, "idea", role="assistant", actions=[card])
        response = await call(w.owner, "POST", apply_path(card["id"]))
        assert response.status_code == 200, response.text
        assert response.json()["idea"]["title"] == "Rooftop picnic"


async def test_an_unknown_hidden_or_unseen_action_is_a_404(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        assert (await call(w.owner, "POST", apply_path(uuid.uuid4()))).status_code == 404
        mine, theirs = await threads_of(w.owner), await threads_of(w.partner)
        private = action("idea", idea_payload())
        await add_message(w, theirs["private"], "x", role="assistant", actions=[private])
        assert (await call(w.owner, "POST", apply_path(private["id"]))).status_code == 404
        hidden = action("idea", idea_payload())
        await add_message(
            w, mine["shared"], "x", role="assistant", actions=[hidden], hidden_for=w.partner_id
        )
        assert (await call(w.partner, "POST", apply_path(hidden["id"]))).status_code == 404
        async with w.app.state.sessionmaker() as session:
            assert (await session.execute(select(Idea))).first() is None


async def test_apply_needs_a_csrf_header(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await w.owner.get("/api/v1/meta")
        response = await w.owner.post(apply_path(uuid.uuid4()))
        assert response.status_code == 403


async def test_the_planner_applies_an_itinerary_change(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(
            w,
            stops=[{"title": "Old stop", "starts_at": when(5), "ends_at": when(5, 19)}],
        )
        thread = (await threads_of(w.owner))["private"]
        payload = {
            "itinerary_id": str(itinerary_id),
            "description": "Tacos instead",
            "stops": [stop_payload()],
        }
        card = action("itinerary_change", payload)
        await add_message(w, thread, "change", role="assistant", actions=[card])
        response = await call(w.owner, "POST", apply_path(card["id"]))
        assert response.status_code == 200, response.text
        out = response.json()
        assert [s["title"] for s in out["itinerary"]["stops"]] == ["Tacos"]
        assert out["action"]["result"] == {"itinerary_id": str(itinerary_id)}
        async with w.app.state.sessionmaker() as session:
            titles = (await session.execute(select(Stop.title))).scalars().all()
        assert list(titles) == ["Tacos"]


async def test_the_partner_cannot_apply_an_itinerary_change_and_it_stays_unapplied(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(
            w,
            status="scheduled",
            announced_at=when(-1),
            starts_at=when(5),
            stops=[{"title": "Old stop", "starts_at": when(5), "ends_at": when(5, 19)}],
        )
        thread = (await threads_of(w.owner))["shared"]
        payload = {"itinerary_id": str(itinerary_id), "description": "x", "stops": [stop_payload()]}
        card = action("itinerary_change", payload)
        message_id = await add_message(w, thread, "change", role="assistant", actions=[card])
        response = await call(w.partner, "POST", apply_path(card["id"]))
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"
        assert (await stored_actions(w, message_id))[0]["applied_at"] is None
        async with w.app.state.sessionmaker() as session:
            titles = (await session.execute(select(Stop.title))).scalars().all()
        assert list(titles) == ["Old stop"]


async def test_an_itinerary_change_without_stops_is_409_nothing_to_apply(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w)
        thread = (await threads_of(w.owner))["private"]
        payload = {"itinerary_id": str(itinerary_id), "description": "words only", "stops": None}
        card = action("itinerary_change", payload)
        await add_message(w, thread, "change", role="assistant", actions=[card])
        response = await call(w.owner, "POST", apply_path(card["id"]))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "nothing_to_apply"


async def test_a_calendar_slot_schedules_the_itinerary(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(
            w, stops=[{"title": "Dinner", "starts_at": when(5), "ends_at": when(5, 20)}]
        )
        thread = (await threads_of(w.owner))["private"]
        start = when(days=5, hour=18)
        payload = {
            "starts_at": start.isoformat(),
            "ends_at": None,
            "itinerary_id": str(itinerary_id),
            "idea_id": None,
        }
        card = action("calendar_slot", payload)
        await add_message(w, thread, "slot", role="assistant", actions=[card])
        response = await call(w.owner, "POST", apply_path(card["id"]))
        assert response.status_code == 200, response.text
        out = response.json()["itinerary"]
        assert out["status"] == "scheduled" and out["is_surprise"] is False
        assert dt.datetime.fromisoformat(out["starts_at"]) == start
        assert response.json()["action"]["result"] == {"itinerary_id": str(itinerary_id)}


async def test_a_calendar_slot_keeps_an_unrevealed_surprise_hidden(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(
            w,
            title="Secret Night",
            status="scheduled",
            is_surprise=True,
            starts_at=when(5, 18),
            reveal_at=when(5, 18),
        )
        thread = (await threads_of(w.owner))["private"]
        start = when(days=6, hour=18)
        payload = {
            "starts_at": start.isoformat(),
            "ends_at": None,
            "itinerary_id": str(itinerary_id),
            "idea_id": None,
        }
        card = action("calendar_slot", payload)
        await add_message(w, thread, "slot", role="assistant", actions=[card])
        response = await call(w.owner, "POST", apply_path(card["id"]))
        assert response.status_code == 200, response.text
        assert response.json()["itinerary"]["is_surprise"] is True

        async with w.app.state.sessionmaker() as session:
            row = await session.get(Itinerary, itinerary_id)
            assert row is not None
            assert row.is_surprise is True and row.announced_at is None
            assert row.starts_at == start
        # The partner still cannot see it.
        assert (await w.partner.get(f"/api/v1/itineraries/{itinerary_id}")).status_code == 404
        assert "Secret Night" not in (await w.partner.get("/api/v1/itineraries")).text


async def test_the_partner_cannot_schedule_the_planners_itinerary(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w, status="scheduled", announced_at=when(-1), starts_at=when(5))
        thread = (await threads_of(w.owner))["shared"]
        payload = {
            "starts_at": when(days=6).isoformat(),
            "ends_at": None,
            "itinerary_id": str(itinerary_id),
            "idea_id": None,
        }
        card = action("calendar_slot", payload)
        await add_message(w, thread, "slot", role="assistant", actions=[card])
        assert (await call(w.partner, "POST", apply_path(card["id"]))).status_code == 403


async def test_a_calendar_slot_with_an_idea_builds_a_draft(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        async with w.app.state.sessionmaker() as session:
            idea = Idea(
                couple_id=w.couple_id,
                created_by_user_id=w.owner_id,
                source="manual",
                visibility="shared",
                title="Rooftop picnic",
                pitch="Sunset and snacks.",
            )
            session.add(idea)
            await session.commit()
            idea_id = idea.id
        thread = (await threads_of(w.owner))["shared"]
        start = when(days=5, hour=18)
        payload = {
            "starts_at": start.isoformat(),
            "ends_at": None,
            "itinerary_id": None,
            "idea_id": str(idea_id),
        }
        card = action("calendar_slot", payload)
        await add_message(w, thread, "slot", role="assistant", actions=[card])
        response = await call(w.owner, "POST", apply_path(card["id"]))
        assert response.status_code == 200, response.text
        out = response.json()["itinerary"]
        assert out["status"] == "draft" and out["idea_id"] == str(idea_id) and out["stops"]
        async with w.app.state.sessionmaker() as session:
            rows = (await session.execute(select(Itinerary))).scalars().all()
        assert [str(r.id) for r in rows] == [out["id"]]
        assert response.json()["action"]["result"] == {"itinerary_id": out["id"]}


async def test_a_calendar_slot_with_neither_itinerary_nor_idea_is_409(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        thread = (await threads_of(w.owner))["private"]
        payload = {
            "starts_at": when(days=5).isoformat(),
            "ends_at": None,
            "itinerary_id": None,
            "idea_id": None,
        }
        card = action("calendar_slot", payload)
        await add_message(w, thread, "slot", role="assistant", actions=[card])
        response = await call(w.owner, "POST", apply_path(card["id"]))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "nothing_to_apply"


async def test_a_calendar_slot_with_an_idea_is_503_when_the_ai_is_unavailable(
    db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.chat.get_ai_client", lambda settings: None)
    async with world(db_app) as w:
        thread = (await threads_of(w.owner))["private"]
        payload = {
            "starts_at": when(days=5).isoformat(),
            "ends_at": None,
            "itinerary_id": None,
            "idea_id": str(uuid.uuid4()),
        }
        card = action("calendar_slot", payload)
        await add_message(w, thread, "slot", role="assistant", actions=[card])
        response = await call(w.owner, "POST", apply_path(card["id"]))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "ai_unavailable"
