"""The shared deck, votes, undo and the match lists."""

import datetime as dt
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Idea, Itinerary, Vote
from app.services import ideas as idea_service
from tests.authutil import call, joined_couple, sign_in
from tests.test_ideas_api import (
    IDEAS,
    OWNER,
    add_idea,
    couple_id,
    error_code,
    insert_idea,
    share,
    shared_idea,
    user_id,
)

pytestmark = pytest.mark.db

DECK = "/api/v1/deck"
UNDO = "/api/v1/deck/undo"
LISTS = f"{IDEAS}/lists"


async def vote(client: AsyncClient, idea_id: str, value: str) -> Any:
    return await call(client, "PUT", f"{IDEAS}/{idea_id}/vote", {"value": value})


async def deck_titles(client: AsyncClient) -> list[str]:
    response = await client.get(DECK)
    assert response.status_code == 200, response.text
    return [c["title"] for c in response.json()["cards"]]


async def list_titles(client: AsyncClient) -> dict[str, list[str]]:
    response = await client.get(LISTS)
    assert response.status_code == 200, response.text
    return {name: [i["title"] for i in items] for name, items in response.json().items()}


async def schedule_itinerary(db: AsyncSession, idea_id: str, status: str = "scheduled") -> None:
    db.add(
        Itinerary(
            couple_id=await couple_id(db),
            idea_id=uuid.UUID(idea_id),
            planner_user_id=await user_id(db, OWNER),
            title="The date",
            status=status,
        )
    )
    await db.commit()


async def age_vote(db: AsyncSession, idea_id: str, email: str, minutes: int) -> None:
    await db.execute(
        update(Vote)
        .where(Vote.idea_id == uuid.UUID(idea_id), Vote.user_id == await user_id(db, email))
        .values(updated_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=minutes))
    )
    await db.commit()


async def test_the_deck_is_the_shared_active_ideas_i_have_not_voted_on_oldest_first(
    db_app: FastAPI,
) -> None:
    async with joined_couple(db_app) as (owner, partner):
        await add_idea(owner, "Private one")
        first = await shared_idea(owner, "First")
        await shared_idea(partner, "Second")
        third = await shared_idea(owner, "Third")
        archived = await shared_idea(owner, "Archived")
        await call(owner, "DELETE", f"{IDEAS}/{archived['id']}")

        assert await deck_titles(owner) == ["First", "Second", "Third"]
        assert await deck_titles(partner) == ["First", "Second", "Third"]
        assert (await vote(owner, first["id"], "no")).status_code == 200
        assert await deck_titles(owner) == ["Second", "Third"]
        assert await deck_titles(partner) == ["First", "Second", "Third"]
        assert (await vote(partner, third["id"], "maybe")).status_code == 200
        assert await deck_titles(partner) == ["First", "Second"]
        assert (await owner.get(DECK)).json()["partner_joined"] is True


async def test_the_deck_holds_at_most_twenty_cards(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (owner, _):
        for n in range(22):
            await shared_idea(owner, f"Idea {n:02d}")
        assert await deck_titles(owner) == [f"Idea {n:02d}" for n in range(20)]


async def test_an_unpaired_user_gets_an_empty_deck_and_partner_joined_false(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    await sign_in(db_app, db_client, OWNER)
    await shared_idea(db_client, "Waiting")
    assert (await db_client.get(DECK)).json() == {"cards": [], "partner_joined": False}


async def test_voting_upserts_and_hides_the_partners_vote_until_both_have_voted(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with joined_couple(db_app) as (owner, partner):
        idea = await shared_idea(owner, "Concert")
        first = await vote(owner, idea["id"], "maybe")
        assert first.status_code == 200
        assert first.json()["votes"] == {"mine": "maybe", "partner": None}
        again = await vote(owner, idea["id"], "yes")
        assert again.json()["votes"] == {"mine": "yes", "partner": None}
        assert len((await db_session.execute(select(Vote))).scalars().all()) == 1

        # The partner has not voted, so they see nothing of the owner's vote either.
        seen = (await partner.get(IDEAS, params={"visibility": "shared"})).json()[0]
        assert seen["votes"] == {"mine": None, "partner": None}
        # The owner still doesn't see an answer that isn't there.
        mine = (await owner.get(IDEAS, params={"visibility": "shared"})).json()[0]
        assert mine["votes"] == {"mine": "yes", "partner": None}

        # Once both have voted, both see both.
        both = await vote(partner, idea["id"], "no")
        assert both.json()["votes"] == {"mine": "no", "partner": "yes"}
        mine = (await owner.get(IDEAS, params={"visibility": "shared"})).json()[0]
        assert mine["votes"] == {"mine": "yes", "partner": "no"}


async def test_a_vote_needs_a_valid_value_and_a_shared_idea(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    await sign_in(db_app, db_client, OWNER)
    private = await add_idea(db_client, "Mine alone")
    assert (await vote(db_client, private["id"], "yes")).status_code == 404  # even the creator
    shared = await share(db_client, private["id"])
    assert (await vote(db_client, shared["id"], "love")).status_code == 422
    assert (await vote(db_client, shared["id"], "yes")).status_code == 200


async def test_match_hook_fires_on_the_transition_into_a_match_only(
    db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, uuid.UUID]] = []

    async def record(
        session: AsyncSession, idea: Idea, last_voter: uuid.UUID, settings: object
    ) -> None:
        calls.append((idea.title, last_voter))

    monkeypatch.setattr(idea_service, "on_match", record)
    async with joined_couple(db_app) as (owner, partner):
        idea = await shared_idea(owner, "Dinner")
        await vote(owner, idea["id"], "yes")
        assert calls == []
        await vote(partner, idea["id"], "maybe")
        assert calls == []
        partner_id = (await partner.get("/api/v1/me")).json()["id"]
        await vote(partner, idea["id"], "yes")
        assert calls == [("Dinner", uuid.UUID(partner_id))]
        # Re-voting yes on an existing match is not a new match.
        await vote(partner, idea["id"], "yes")
        await vote(owner, idea["id"], "yes")
        assert len(calls) == 1
        # Falling out of the match and back in is.
        await vote(owner, idea["id"], "maybe")
        await vote(owner, idea["id"], "yes")
        assert [c[0] for c in calls] == ["Dinner", "Dinner"]
        owner_id = (await owner.get("/api/v1/me")).json()["id"]
        assert calls[1][1] == uuid.UUID(owner_id)


async def test_one_yes_is_not_a_match(db_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def record(
        session: AsyncSession, idea: Idea, last_voter: uuid.UUID, settings: object
    ) -> None:
        calls.append(idea.title)

    monkeypatch.setattr(idea_service, "on_match", record)
    async with joined_couple(db_app) as (owner, partner):
        idea = await shared_idea(owner, "Hike")
        await vote(owner, idea["id"], "yes")
        await vote(partner, idea["id"], "no")
        assert calls == []


async def test_voting_is_locked_once_a_date_from_the_idea_is_scheduled(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with joined_couple(db_app) as (owner, partner):
        idea = await shared_idea(owner, "Gala")
        await schedule_itinerary(db_session, idea["id"], status="draft")
        assert (await vote(owner, idea["id"], "yes")).status_code == 200  # a draft doesn't lock
        await schedule_itinerary(db_session, idea["id"], status="scheduled")
        for client, value in ((owner, "no"), (partner, "yes")):
            response = await vote(client, idea["id"], value)
            assert response.status_code == 409
            assert error_code(response) == "vote_locked"
        mine = (await owner.get(IDEAS, params={"visibility": "shared"})).json()[0]
        assert mine["votes"]["mine"] == "yes"  # unchanged


async def test_undo_takes_back_my_latest_vote(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (owner, partner):
        first = await shared_idea(owner, "First")
        second = await shared_idea(owner, "Second")
        assert (await call(owner, "POST", UNDO)).status_code == 204  # nothing yet

        await vote(owner, first["id"], "yes")
        await vote(owner, second["id"], "no")
        await vote(partner, first["id"], "yes")  # the partner's votes are not mine to undo
        assert await deck_titles(owner) == []

        undone = await call(owner, "POST", UNDO)
        assert undone.status_code == 200
        assert undone.json()["id"] == second["id"]
        assert undone.json()["votes"] == {"mine": None, "partner": None}
        assert await deck_titles(owner) == ["Second"]

        again = await call(owner, "POST", UNDO)
        assert again.json()["id"] == first["id"]
        assert await deck_titles(owner) == ["First", "Second"]
        assert (await call(owner, "POST", UNDO)).status_code == 204
        assert (await call(partner, "POST", UNDO)).json()["id"] == first["id"]


async def test_undo_honours_the_ten_minute_window(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with joined_couple(db_app) as (owner, _):
        idea = await shared_idea(owner, "Late")
        await vote(owner, idea["id"], "yes")

        await age_vote(db_session, idea["id"], OWNER, minutes=11)
        assert (await call(owner, "POST", UNDO)).status_code == 204
        assert await deck_titles(owner) == []  # the vote stands

        await age_vote(db_session, idea["id"], OWNER, minutes=9)
        assert (await call(owner, "POST", UNDO)).status_code == 200
        assert await deck_titles(owner) == ["Late"]


async def test_undo_is_refused_once_the_idea_is_locked(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with joined_couple(db_app) as (owner, _):
        idea = await shared_idea(owner, "Booked")
        await vote(owner, idea["id"], "yes")
        await schedule_itinerary(db_session, idea["id"])
        assert (await call(owner, "POST", UNDO)).status_code == 204
        assert await deck_titles(owner) == []


async def test_lists_bucket_by_how_the_votes_stand(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with joined_couple(db_app) as (owner, partner):
        votes = {  # title: (owner's vote, partner's vote)
            "Match": ("yes", "yes"),
            "Maybe one": ("yes", "maybe"),
            "Maybe two": ("maybe", "yes"),
            "Maybe both": ("maybe", "maybe"),
            "Passed by partner": ("yes", "no"),
            "Passed by me": ("no", "maybe"),
            "Passed by both": ("no", "no"),
            "Waiting": ("maybe", None),
            "Waiting no": ("no", None),
            "Not voted by me": (None, "yes"),
            "Not voted by anyone": (None, None),
        }
        for title, (mine, theirs) in votes.items():
            idea = await shared_idea(owner, title)
            if mine:
                await vote(owner, idea["id"], mine)
            if theirs:
                await vote(partner, idea["id"], theirs)
        # Archived and private ideas never show up, however they were voted on.
        gone = await shared_idea(owner, "Archived match")
        await vote(owner, gone["id"], "yes")
        await vote(partner, gone["id"], "yes")
        await call(owner, "DELETE", f"{IDEAS}/{gone['id']}")
        await add_idea(owner, "Private")

        mine = await list_titles(owner)
        assert {k: sorted(v) for k, v in mine.items()} == {
            "matches": ["Match"],
            "maybes": ["Maybe both", "Maybe one", "Maybe two"],
            "pending_partner": ["Waiting", "Waiting no"],
            "passed": ["Passed by both", "Passed by me", "Passed by partner"],
        }

        theirs = await list_titles(partner)
        assert theirs["pending_partner"] == ["Not voted by me"]
        assert theirs["matches"] == ["Match"]
        assert sorted(theirs["passed"]) == ["Passed by both", "Passed by me", "Passed by partner"]

        # Vote values follow the same hiding rule inside the lists.
        pending = (await owner.get(LISTS)).json()["pending_partner"]
        assert all(i["votes"]["partner"] is None for i in pending)
        matches = (await owner.get(LISTS)).json()["matches"]
        assert matches[0]["votes"] == {"mine": "yes", "partner": "yes"}


async def test_an_expired_match_stays_in_the_lists(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with joined_couple(db_app) as (owner, partner):
        idea = await shared_idea(owner, "Festival")
        await vote(owner, idea["id"], "yes")
        await vote(partner, idea["id"], "yes")
        await db_session.execute(update(Idea).values(status="expired"))
        await db_session.commit()
        assert (await list_titles(owner))["matches"] == ["Festival"]
        assert await deck_titles(owner) == []


async def test_lists_are_empty_for_an_unpaired_user_without_votes(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    await sign_in(db_app, db_client, OWNER)
    assert await list_titles(db_client) == {
        "matches": [],
        "maybes": [],
        "pending_partner": [],
        "passed": [],
    }


async def test_undo_never_touches_a_vote_on_a_private_idea(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await sign_in(db_app, db_client, OWNER)
    private = await insert_idea(db_session, title="Hidden", visibility="private")
    db_session.add(Vote(idea_id=private.id, user_id=await user_id(db_session, OWNER), value="yes"))
    await db_session.commit()
    assert (await call(db_client, "POST", UNDO)).status_code == 204
    db_session.expire_all()
    assert len((await db_session.execute(select(Vote))).scalars().all()) == 1


@pytest.mark.parametrize("status", ["archived", "expired"])
async def test_voting_on_an_idea_that_is_not_active_is_a_409(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession, status: str
) -> None:
    await sign_in(db_app, db_client, OWNER)
    idea = await insert_idea(db_session, title="Gone", status=status)
    response = await vote(db_client, str(idea.id), "yes")
    assert response.status_code == 409
    assert error_code(response) == "idea_not_active"
    assert (await db_session.execute(select(Vote))).scalars().all() == []
