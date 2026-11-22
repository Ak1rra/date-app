"""M7: ratings, the planner's read, the ratings read and past dates."""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import String, cast, delete, func, select, update

from app import realtime
from app.models import CoupleMember, Job, PartnerProfile, Rating, Stop
from app.worker import handlers
from tests.authutil import call
from tests.itinerary_helpers import ITINERARIES, World, seed, when, world

pytestmark = pytest.mark.db

PAST_DATES = "/api/v1/past-dates"


def rating_url(itinerary_id: uuid.UUID) -> str:
    return f"{ITINERARIES}/{itinerary_id}/rating"


def ratings_url(itinerary_id: uuid.UUID) -> str:
    return f"{ITINERARIES}/{itinerary_id}/ratings"


def read_url(itinerary_id: uuid.UUID) -> str:
    return f"{ITINERARIES}/{itinerary_id}/planner-read"


async def seed_completed(w: World, **fields: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "title": "Rooftop dinner",
        "status": "completed",
        "starts_at": when(-2, 18),
        "ends_at": when(-2, 21),
        "announced_at": when(-5, 12),
    }
    values.update(fields)
    return await seed(w, **values)


async def stop_ids(app: FastAPI, itinerary_id: uuid.UUID) -> list[uuid.UUID]:
    async with app.state.sessionmaker() as session:
        return list(
            (
                await session.execute(
                    select(Stop.id).where(Stop.itinerary_id == itinerary_id).order_by(Stop.position)
                )
            )
            .scalars()
            .all()
        )


async def test_rating_roundtrip_and_upsert(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_completed(w, stops=[{"title": "Dinner"}, {"title": "Gelato"}])
        first, second = await stop_ids(w.app, itinerary_id)
        response = await call(
            w.partner,
            "PUT",
            rating_url(itinerary_id),
            {
                "stars": 4,
                "would_again": True,
                "comment": "  lovely  ",
                "stop_thumbs": {str(first): "up", str(second): "down"},
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["stars"] == 4 and body["would_again"] is True
        assert body["comment"] == "lovely"
        assert body["stop_thumbs"] == {str(first): "up", str(second): "down"}
        created = body["updated_at"]

        again = await call(
            w.partner,
            "PUT",
            rating_url(itinerary_id),
            {"stars": 2, "would_again": False, "comment": "   "},
        )
        assert again.status_code == 200, again.text
        assert again.json()["comment"] is None
        assert again.json()["stop_thumbs"] == {}
        assert again.json()["updated_at"] >= created
        async with w.app.state.sessionmaker() as session:
            rows = (await session.execute(select(Rating))).scalars().all()
            assert len(rows) == 1 and rows[0].stars == 2


@pytest.mark.parametrize("stars", [0, 6])
async def test_rating_stars_must_be_one_to_five(db_app: FastAPI, stars: int) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_completed(w)
        response = await call(
            w.owner, "PUT", rating_url(itinerary_id), {"stars": stars, "would_again": True}
        )
        assert response.status_code == 422


async def test_rating_comment_over_280_is_rejected(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_completed(w)
        response = await call(
            w.owner,
            "PUT",
            rating_url(itinerary_id),
            {"stars": 3, "would_again": True, "comment": "x" * 281},
        )
        assert response.status_code == 422


async def test_rating_unknown_stop_is_422(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_completed(w, stops=[{"title": "Dinner"}])
        other_id = await seed_completed(w, stops=[{"title": "Elsewhere"}])
        (foreign,) = await stop_ids(w.app, other_id)
        for stop in (uuid.uuid4(), foreign):
            response = await call(
                w.owner,
                "PUT",
                rating_url(itinerary_id),
                {"stars": 3, "would_again": True, "stop_thumbs": {str(stop): "up"}},
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "unknown_stop"


async def test_rating_requires_a_completed_date(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_completed(w, status="scheduled")
        response = await call(
            w.owner, "PUT", rating_url(itinerary_id), {"stars": 3, "would_again": True}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_completed"


async def test_a_hidden_date_is_404(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_completed(w, announced_at=None)  # the owner's private draft
        rated = await call(
            w.partner, "PUT", rating_url(itinerary_id), {"stars": 3, "would_again": True}
        )
        assert rated.status_code == 404
        assert (await call(w.partner, "GET", ratings_url(itinerary_id))).status_code == 404
        read = await call(w.partner, "PUT", read_url(itinerary_id), {"enjoyment": 3})
        assert read.status_code == 404


async def test_partner_rating_is_hidden_until_both_have_rated(
    db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    published: list[str] = []

    async def record(couple_id: uuid.UUID, event: str, payload: dict[str, Any], **_: Any) -> None:
        published.append(event)

    monkeypatch.setattr(realtime.hub, "publish", record)
    async with world(db_app) as w:
        itinerary_id = await seed_completed(w)
        await call(w.owner, "PUT", rating_url(itinerary_id), {"stars": 5, "would_again": True})
        while realtime._tasks:
            await next(iter(realtime._tasks))

        before = (await call(w.partner, "GET", ratings_url(itinerary_id))).json()
        assert before["mine"] is None
        assert before["partner"] is None
        assert before["partner_revealed"] is False
        assert "rating.revealed" not in published

        await call(w.partner, "PUT", rating_url(itinerary_id), {"stars": 3, "would_again": False})
        while realtime._tasks:
            await next(iter(realtime._tasks))
        assert published.count("rating.revealed") == 1

        after = (await call(w.partner, "GET", ratings_url(itinerary_id))).json()
        assert after["partner_revealed"] is True
        assert after["mine"]["stars"] == 3
        assert after["partner"]["stars"] == 5
        owner_view = (await call(w.owner, "GET", ratings_url(itinerary_id))).json()
        assert owner_view["partner"]["stars"] == 3


async def test_partner_rating_is_shown_a_week_after_the_date_ends(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        recent = await seed_completed(w, starts_at=when(-6, 18), ends_at=when(-6, 21))
        old = await seed_completed(w, starts_at=when(-9, 18), ends_at=when(-9, 21))
        for itinerary_id in (recent, old):
            await call(w.owner, "PUT", rating_url(itinerary_id), {"stars": 5, "would_again": True})

        hidden = (await call(w.partner, "GET", ratings_url(recent))).json()
        assert hidden["partner_revealed"] is False and hidden["partner"] is None
        shown = (await call(w.partner, "GET", ratings_url(old))).json()
        assert shown["partner_revealed"] is True
        assert shown["partner"]["stars"] == 5
        assert shown["mine"] is None


async def test_planner_read_is_planner_only(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_completed(w)
        forbidden = await call(w.partner, "PUT", read_url(itinerary_id), {"enjoyment": 4})
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "forbidden"

        saved = await call(
            w.owner,
            "PUT",
            read_url(itinerary_id),
            {"enjoyment": 4, "highlights": " the view ", "misses": ""},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["enjoyment"] == 4
        assert saved.json()["highlights"] == "the view"
        assert saved.json()["misses"] is None
        redone = await call(w.owner, "PUT", read_url(itinerary_id), {"enjoyment": 2})
        assert redone.json()["enjoyment"] == 2 and redone.json()["highlights"] is None

        for body in ({"enjoyment": 0}, {"enjoyment": 6}, {"enjoyment": 3, "misses": "x" * 1001}):
            assert (await call(w.owner, "PUT", read_url(itinerary_id), body)).status_code == 422


async def test_planner_read_needs_a_completed_date(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_completed(w, status="scheduled")
        response = await call(w.owner, "PUT", read_url(itinerary_id), {"enjoyment": 4})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_completed"


async def test_ratings_expose_the_planner_read_only_to_the_planner(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_completed(w)
        await call(w.owner, "PUT", read_url(itinerary_id), {"enjoyment": 5, "misses": "parking"})
        mine = (await call(w.owner, "GET", ratings_url(itinerary_id))).json()
        assert mine["planner_read"]["enjoyment"] == 5
        assert mine["planner_read"]["misses"] == "parking"
        theirs = (await call(w.partner, "GET", ratings_url(itinerary_id))).json()
        assert theirs["planner_read"] is None


async def test_past_dates_lists_completed_newest_first_with_visible_feedback(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        older = await seed_completed(
            w, title="Older", starts_at=when(-20, 18), ends_at=when(-20, 21)
        )
        newer = await seed_completed(w, title="Newer", starts_at=when(-2, 18), ends_at=when(-2, 21))
        await seed_completed(
            w, title="Not yet", status="scheduled", starts_at=when(3, 18), ends_at=when(3, 21)
        )
        await seed_completed(w, title="Hidden", announced_at=None, starts_at=when(-30, 18))
        await seed_completed(w, title="Theirs", planner="partner", starts_at=when(-40, 18))

        await call(w.owner, "PUT", rating_url(older), {"stars": 5, "would_again": True})
        await call(w.partner, "PUT", rating_url(older), {"stars": 2, "would_again": False})
        await call(w.partner, "PUT", rating_url(newer), {"stars": 4, "would_again": True})
        await call(w.owner, "PUT", read_url(newer), {"enjoyment": 3, "highlights": "tacos"})

        owner_rows = (await call(w.owner, "GET", PAST_DATES)).json()
        assert [row["title"] for row in owner_rows] == ["Newer", "Older", "Hidden", "Theirs"]
        by_title = {row["title"]: row for row in owner_rows}
        assert by_title["Newer"]["id"] == str(newer)
        assert by_title["Newer"]["planner_user_id"] == str(w.owner_id)
        assert by_title["Newer"]["is_surprise"] is False
        assert by_title["Newer"]["mine"] is None
        assert by_title["Newer"]["partner"] is None  # not revealed: the owner hasn't rated
        assert by_title["Newer"]["planner_read"]["enjoyment"] == 3
        assert by_title["Older"]["mine"]["stars"] == 5
        assert by_title["Older"]["partner"]["stars"] == 2
        assert by_title["Older"]["planner_read"] is None

        partner_rows = (await call(w.partner, "GET", PAST_DATES)).json()
        assert [row["title"] for row in partner_rows] == ["Newer", "Older", "Theirs"]
        assert partner_rows[0]["planner_read"] is None
        assert partner_rows[0]["mine"]["stars"] == 4
        assert partner_rows[0]["partner"] is None
        assert partner_rows[1]["partner"]["stars"] == 5


async def test_past_dates_is_empty_without_completed_dates(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await seed_completed(w, status="scheduled")
        assert (await call(w.owner, "GET", PAST_DATES)).json() == []


async def test_a_departed_members_rating_is_never_shown_to_the_other_member(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_completed(w, starts_at=when(-9, 18), ends_at=when(-9, 21))
        await call(w.partner, "PUT", rating_url(itinerary_id), {"stars": 1, "would_again": False})
        async with w.app.state.sessionmaker() as session:
            await session.execute(
                update(CoupleMember)
                .where(CoupleMember.user_id == w.partner_id)
                .values(left_at=func.now())
            )
            await session.commit()

        seen = (await call(w.owner, "GET", ratings_url(itinerary_id))).json()
        assert seen["partner_revealed"] is True  # the week has passed
        assert seen["partner"] is None
        (row,) = (await call(w.owner, "GET", PAST_DATES)).json()
        assert row["partner"] is None


async def test_reveal_is_emitted_once_by_the_rating_that_completes_the_set(
    db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    published: list[str] = []

    async def record(couple_id: uuid.UUID, event: str, payload: dict[str, Any], **_: Any) -> None:
        published.append(event)

    monkeypatch.setattr(realtime.hub, "publish", record)

    async def drain() -> None:
        while realtime._tasks:
            await next(iter(realtime._tasks))

    async with world(db_app) as w:
        itinerary_id = await seed_completed(w)
        body = {"stars": 4, "would_again": True}
        await call(w.owner, "PUT", rating_url(itinerary_id), body)
        await call(w.owner, "PUT", rating_url(itinerary_id), body)  # an edit before the set
        await drain()
        assert published.count("rating.revealed") == 0

        await call(w.partner, "PUT", rating_url(itinerary_id), body)
        await drain()
        assert published.count("rating.revealed") == 1

        await call(w.partner, "PUT", rating_url(itinerary_id), {"stars": 2, "would_again": False})
        await call(w.owner, "PUT", rating_url(itinerary_id), body)
        await drain()
        assert published.count("rating.revealed") == 1


async def test_the_reveal_job_stays_quiet_when_both_already_rated(
    db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    published: list[str] = []

    async def record(couple_id: uuid.UUID, event: str, payload: dict[str, Any], **_: Any) -> None:
        published.append(event)

    monkeypatch.setattr(realtime.hub, "publish", record)
    async with world(db_app) as w:
        both = await seed_completed(w)
        one = await seed_completed(w)
        body = {"stars": 4, "would_again": True}
        for client in (w.owner, w.partner):
            await call(client, "PUT", rating_url(both), body)
        await call(w.owner, "PUT", rating_url(one), body)
        while realtime._tasks:
            await next(iter(realtime._tasks))
        published.clear()

        for itinerary_id in (both, one):
            async with w.app.state.sessionmaker() as session:
                await handlers.ratings_reveal(session, {"itinerary_id": str(itinerary_id)})
                await session.commit()
            while realtime._tasks:
                await next(iter(realtime._tasks))
        assert published == ["rating.revealed"]  # only for the date with a missing rating


async def taste_jobs_by_user(app: FastAPI) -> list[uuid.UUID]:
    """The subject user of each queued `taste.recompute` job."""
    async with app.state.sessionmaker() as session:
        rows = await session.execute(
            select(PartnerProfile.subject_user_id)
            .join(Job, Job.payload["profile_id"].astext == cast(PartnerProfile.id, String))
            .where(Job.kind == "taste.recompute")
        )
        return [user_id for (user_id,) in rows.all()]


async def test_feedback_recomputes_the_taste_of_the_person_it_describes(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed_completed(w)
        rated = await call(
            w.partner, "PUT", rating_url(itinerary_id), {"stars": 4, "would_again": True}
        )
        assert rated.status_code == 200, rated.text
        assert await taste_jobs_by_user(w.app) == [w.partner_id]

        async with w.app.state.sessionmaker() as session:
            await session.execute(delete(Job).where(Job.kind == "taste.recompute"))
            await session.commit()
        read = await call(w.owner, "PUT", read_url(itinerary_id), {"enjoyment": 4})
        assert read.status_code == 200, read.text
        assert await taste_jobs_by_user(w.app) == [w.partner_id]
