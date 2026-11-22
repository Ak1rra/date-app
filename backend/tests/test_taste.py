"""The taste summary (AI-6): scheduling, the counts, the recompute job and the vote hook."""

import datetime as dt
import json
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.ai.fakes  # noqa: F401  (registers the fake responders)
from app.ai import caps
from app.ai.client import FAKE_RESPONDERS, AIError, FakeAIClient, Responder
from app.ai.context import build_context
from app.ai.prompts import TASTE_SYSTEM
from app.ai.schemas import TasteSummaryOut
from app.errors import ApiError
from app.models import (
    AIUsage,
    Idea,
    Itinerary,
    Job,
    PlannerRead,
    Rating,
    TasteSummary,
    Vote,
)
from app.services import taste
from app.worker import handlers
from tests.authutil import call, joined_couple
from tests.conftest import make_settings
from tests.test_ai_context import Fixture, seed
from tests.test_deck_votes import vote
from tests.test_ideas_api import shared_idea

pytestmark = pytest.mark.db

NOW = dt.datetime(2030, 6, 15, 3, 0, tzinfo=dt.UTC)  # on a 10-minute boundary


class Counting:
    """A responder that records the user messages it was sent."""

    def __init__(self, output: TasteSummaryOut | None = None) -> None:
        self.output = output or TasteSummaryOut(
            summary="Likes quiet dates.", loves=["cafes"], avoid=[]
        )
        self.messages: list[str] = []

    @property
    def responders(self) -> dict[Any, Responder]:
        async def respond(tools: Any, context: str, messages: Any) -> TasteSummaryOut:
            self.messages.append(messages[-1]["content"])
            return self.output

        return {TasteSummaryOut: respond}

    def client(self) -> FakeAIClient:
        return FakeAIClient(self.responders)

    def counts(self) -> dict[str, Any]:
        content = self.messages[-1]
        return json.loads(content[content.find("{") :])  # type: ignore[no-any-return]


def sessionmaker_of(session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    assert session.bind is not None
    return async_sessionmaker(session.bind, expire_on_commit=False)  # type: ignore[arg-type]


async def run_recompute(session: AsyncSession, fx: Fixture, ai: FakeAIClient) -> None:
    await taste.recompute(session, fx.ann_profile.id, ai, make_settings(), sessionmaker_of(session))
    await session.commit()


async def stored(session: AsyncSession, fx: Fixture) -> TasteSummary | None:
    return (
        await session.execute(
            select(TasteSummary)
            .where(TasteSummary.about_profile_id == fx.ann_profile.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def seed_feedback(session: AsyncSession, fx: Fixture) -> Itinerary:
    """Ann votes on two ideas and rates a completed date; Bea, who planned it, reads it."""
    ideas = [
        Idea(
            couple_id=fx.couple_id,
            created_by_user_id=fx.bea.id,
            source="manual",
            visibility="shared",
            title=title,
            pitch="",
            category=category,
            indoor_outdoor="indoor",
            venue={"price_level": 2},
        )
        for title, category in (("Pottery", "creative"), ("Karaoke", "nightlife"))
    ]
    session.add_all(ideas)
    itinerary = Itinerary(
        couple_id=fx.couple_id, planner_user_id=fx.bea.id, title="The date", status="completed"
    )
    session.add(itinerary)
    await session.flush()
    session.add_all(
        [
            Vote(idea_id=ideas[0].id, user_id=fx.ann.id, value="yes"),
            Vote(idea_id=ideas[1].id, user_id=fx.ann.id, value="no"),
            Rating(
                itinerary_id=itinerary.id,
                user_id=fx.ann.id,
                stars=5,
                would_again=True,
                stop_thumbs={"a": "up", "b": "up", "c": "down"},
            ),
            PlannerRead(
                itinerary_id=itinerary.id,
                planner_user_id=fx.bea.id,
                enjoyment=2,
                highlights="h" * 300,
                misses="the queue",
            ),
        ]
    )
    await session.commit()
    return itinerary


async def job_count(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(Job))).scalar_one()


async def test_schedule_dedupes_within_a_ten_minute_bucket(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    await taste.schedule_recompute(db_session, fx.couple_id, now=NOW)
    await taste.schedule_recompute(db_session, fx.couple_id, now=NOW + dt.timedelta(minutes=9))
    await db_session.commit()
    jobs = (await db_session.execute(select(Job).order_by(Job.dedupe_key))).scalars().all()
    assert len(jobs) == 2  # one per profile
    assert {j.kind for j in jobs} == {"taste.recompute"}
    assert {j.payload["profile_id"] for j in jobs} == {
        str(fx.ann_profile.id),
        str(fx.bea_profile.id),
    }
    assert {j.run_at for j in jobs} == {NOW + dt.timedelta(minutes=2)}

    await taste.schedule_recompute(db_session, fx.couple_id, now=NOW + dt.timedelta(minutes=10))
    await db_session.commit()
    assert await job_count(db_session) == 4


async def test_recompute_without_data_makes_no_ai_call(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    responder = Counting()
    await run_recompute(db_session, fx, responder.client())
    assert responder.messages == []
    assert await stored(db_session, fx) is None
    assert (await db_session.execute(select(func.count()).select_from(AIUsage))).scalar_one() == 0


async def test_recompute_skips_a_profile_nobody_has_joined(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    await seed_feedback(db_session, fx)
    fx.ann_profile.subject_user_id = None
    await db_session.commit()
    responder = Counting()
    await run_recompute(db_session, fx, responder.client())
    assert responder.messages == []


def test_ratings_weigh_twice_the_planners_reads() -> None:
    assert taste.weighted_mean(5.0, 2.0) == 4.0  # (2*5 + 1*2) / 3
    assert taste.weighted_mean(4.0, None) == 4.0
    assert taste.weighted_mean(None, 3.0) == 3.0
    assert taste.weighted_mean(None, None) is None


async def test_recompute_counts_the_feedback_and_upserts(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    await seed_feedback(db_session, fx)
    responder = Counting(TasteSummaryOut(summary="x" * 2000, loves=["pottery"], avoid=["queues"]))
    await run_recompute(db_session, fx, responder.client())

    counts = responder.counts()
    assert counts["votes"]["by_category"] == {
        "creative": {"yes": 1, "no": 0, "maybe": 0},
        "nightlife": {"yes": 0, "no": 1, "maybe": 0},
    }
    assert counts["votes"]["by_price_level"]["2"] == {"yes": 1, "no": 1, "maybe": 0}
    assert counts["votes"]["by_indoor_outdoor"]["indoor"] == {"yes": 1, "no": 1, "maybe": 0}
    assert counts["ratings"]["n"] == 1 and counts["ratings"]["mean_stars"] == 5.0
    assert counts["ratings"]["would_again_rate"] == 1.0
    assert counts["ratings"]["stop_thumbs"] == {"up": 2, "down": 1}
    assert counts["planner_reads"]["mean_enjoyment"] == 2.0
    assert counts["planner_reads"]["highlights"] == ["h" * 200]
    assert counts["planner_reads"]["misses"] == ["the queue"]
    assert counts["weighted_enjoyment"] == 4.0  # ratings 2x, the read 1x

    row = await stored(db_session, fx)
    assert row is not None
    assert row.summary["loves"] == ["pottery"] and row.summary["avoid"] == ["queues"]
    assert len(row.summary["summary"]) == 900
    # Counts only: the planner's free text is sent to the model but never stored.
    assert row.source_counts["planner_reads"] == {"n": 1, "mean_enjoyment": 2.0, "weight": 1}
    assert "the queue" not in json.dumps(row.source_counts) and "hhh" not in json.dumps(
        row.source_counts
    )
    assert {k: v for k, v in counts.items() if k != "planner_reads"} == {
        k: v for k, v in row.source_counts.items() if k != "planner_reads"
    }
    assert "private notes" in TASTE_SYSTEM
    usage = (await db_session.execute(select(AIUsage))).scalar_one()
    assert usage.feature == "taste" and usage.ok

    again = Counting(TasteSummaryOut(summary="Second.", loves=[], avoid=[]))
    await run_recompute(db_session, fx, again.client())
    row = await stored(db_session, fx)
    assert row is not None and row.summary["summary"] == "Second."
    assert (
        await db_session.execute(select(func.count()).select_from(TasteSummary))
    ).scalar_one() == 1


async def test_an_ai_failure_keeps_the_old_summary_and_does_not_raise(
    db_session: AsyncSession,
) -> None:
    fx = await seed(db_session)
    await seed_feedback(db_session, fx)
    db_session.add(
        TasteSummary(
            couple_id=fx.couple_id, about_profile_id=fx.ann_profile.id, summary={"summary": "old"}
        )
    )
    await db_session.commit()

    async def broken(tools: Any, context: str, messages: Any) -> TasteSummaryOut:
        raise AIError("ai_unavailable", "down")

    await run_recompute(db_session, fx, FakeAIClient({TasteSummaryOut: broken}))
    row = await stored(db_session, fx)
    assert row is not None and row.summary == {"summary": "old"}
    usage = (await db_session.execute(select(AIUsage))).scalar_one()
    assert usage.feature == "taste" and not usage.ok  # the failed run is still recorded


async def test_a_reached_ceiling_keeps_the_old_summary_and_does_not_raise(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = await seed(db_session)
    await seed_feedback(db_session, fx)

    async def over(*args: Any, **kwargs: Any) -> None:
        raise ApiError(429, "ai_monthly_cap", "Too much.")

    monkeypatch.setattr(caps, "reserve", over)
    responder = Counting()
    await run_recompute(db_session, fx, responder.client())
    assert responder.messages == []
    assert await stored(db_session, fx) is None


async def test_build_context_surfaces_the_stored_summary(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    await seed_feedback(db_session, fx)
    await run_recompute(db_session, fx, Counting().client())
    raw = await build_context(db_session, fx.couple_id, fx.bea.id, fx.ann_profile.id)
    assert json.loads(raw)["taste"] == {
        "summary": "Likes quiet dates.",
        "loves": ["cafes"],
        "avoid": [],
    }


async def test_the_handler_runs_the_demo_responder(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = await seed(db_session)
    await seed_feedback(db_session, fx)
    assert TasteSummaryOut in FAKE_RESPONDERS
    monkeypatch.setattr(handlers, "get_settings", lambda: make_settings(demo_mode=True))
    await handlers.taste_recompute(db_session, {"profile_id": str(fx.ann_profile.id)})
    await db_session.commit()
    row = await stored(db_session, fx)
    assert row is not None and row.summary["loves"] == ["creative"]

    await handlers.taste_recompute(db_session, {"profile_id": "not-a-uuid"})  # ignored


async def test_the_handler_skips_when_ai_is_unavailable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = await seed(db_session)
    await seed_feedback(db_session, fx)
    monkeypatch.setattr(handlers, "get_settings", lambda: make_settings(demo_mode=False))
    await handlers.taste_recompute(db_session, {"profile_id": str(fx.ann_profile.id)})
    await db_session.commit()
    assert await stored(db_session, fx) is None


async def test_a_vote_queues_a_recompute_for_the_voters_profile_only(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with joined_couple(db_app) as (owner, partner):
        idea = await shared_idea(owner, "Concert")
        assert (await vote(partner, idea["id"], "yes")).status_code == 200
        assert await job_count(db_session) == 1
        assert (await vote(partner, idea["id"], "no")).status_code == 200  # same bucket
        assert await job_count(db_session) == 1
        assert (await vote(owner, idea["id"], "yes")).status_code == 200
        assert await job_count(db_session) == 2
    kinds = (await db_session.execute(select(Job.kind))).scalars().all()
    assert set(kinds) == {"taste.recompute"}


async def test_votes_on_surprise_ideas_are_not_counted(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    await seed_feedback(db_session, fx)
    secret = Idea(
        couple_id=fx.couple_id,
        created_by_user_id=fx.ann.id,
        source="manual",
        visibility="shared",
        title="Secret",
        pitch="",
        category="concert",
        is_surprise=True,
    )
    db_session.add(secret)
    await db_session.flush()
    db_session.add(Vote(idea_id=secret.id, user_id=fx.ann.id, value="yes"))
    await db_session.commit()
    responder = Counting()
    await run_recompute(db_session, fx, responder.client())
    counts = responder.counts()
    assert "concert" not in counts["votes"]["by_category"]
    assert counts["votes"]["n"] == 2


async def test_a_long_summary_is_trimmed_to_120_words(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    await seed_feedback(db_session, fx)
    long = "  ".join(f"w{i}" for i in range(200))
    await run_recompute(
        db_session, fx, Counting(TasteSummaryOut(summary=long, loves=[], avoid=[])).client()
    )
    row = await stored(db_session, fx)
    assert row is not None
    words = row.summary["summary"].split(" ")
    assert len(words) == 120 and words[-1] == "w119"


async def test_a_read_not_by_the_itinerarys_planner_is_ignored(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    itinerary = await seed_feedback(db_session, fx)
    other = Itinerary(
        couple_id=fx.couple_id, planner_user_id=fx.ann.id, title="Ann's", status="completed"
    )
    db_session.add(other)
    await db_session.flush()
    # Bea is not this date's planner, so her read of it doesn't count.
    db_session.add(PlannerRead(itinerary_id=other.id, planner_user_id=fx.bea.id, enjoyment=1))
    await db_session.commit()
    assert itinerary.id != other.id
    responder = Counting()
    await run_recompute(db_session, fx, responder.client())
    reads = responder.counts()["planner_reads"]
    assert reads["n"] == 1 and reads["mean_enjoyment"] == 2.0


async def test_undoing_a_vote_queues_a_recompute(db_app: FastAPI, db_session: AsyncSession) -> None:
    async with joined_couple(db_app) as (owner, partner):
        idea = await shared_idea(owner, "Concert")
        assert (await vote(partner, idea["id"], "yes")).status_code == 200
        await db_session.execute(delete(Job))
        await db_session.commit()
        assert (await call(partner, "POST", "/api/v1/deck/undo")).status_code == 200
        assert await job_count(db_session) == 1


async def test_schedule_for_given_users_only(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    await taste.schedule_recompute(db_session, fx.couple_id, user_ids=[fx.bea.id], now=NOW)
    await db_session.commit()
    jobs = (await db_session.execute(select(Job))).scalars().all()
    assert [j.payload["profile_id"] for j in jobs] == [str(fx.bea_profile.id)]


async def test_a_blank_summary_keeps_the_old_one(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    await seed_feedback(db_session, fx)
    db_session.add(
        TasteSummary(
            couple_id=fx.couple_id, about_profile_id=fx.ann_profile.id, summary={"summary": "old"}
        )
    )
    await db_session.commit()
    blank = Counting(TasteSummaryOut(summary="   \t  ", loves=[], avoid=[]))
    await run_recompute(db_session, fx, blank.client())
    row = await stored(db_session, fx)
    assert row is not None and row.summary == {"summary": "old"}
    usage = (await db_session.execute(select(AIUsage))).scalar_one()
    assert usage.feature == "taste" and not usage.ok
