"""The v1 tables (migration 0002): defaults, constraints and delete rules, checked against the
real database (marker: db)."""

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AIUsage,
    ChatMessage,
    ChatThread,
    ChecklistItem,
    Couple,
    Idea,
    Itinerary,
    Notification,
    PartnerProfile,
    PlannerRead,
    PlanSession,
    ProfileSuggestion,
    ProviderCache,
    Rating,
    Stop,
    TasteSummary,
    TravelLeg,
    User,
    Vote,
)
from tests.test_models import NOW, count, expect_violation, make_couple, make_profile, make_user

pytestmark = pytest.mark.db


def new_idea(couple: Couple, user: User, **kw: object) -> Idea:
    fields: dict[str, object] = {
        "couple_id": couple.id,
        "created_by_user_id": user.id,
        "source": "manual",
        "visibility": "private",
        "title": "Picnic",
        "pitch": "Blanket and snacks",
    }
    return Idea(**{**fields, **kw})


async def make_idea(session: AsyncSession, couple: Couple, user: User) -> Idea:
    idea = new_idea(couple, user)
    session.add(idea)
    await session.commit()
    return idea


async def make_itinerary(session: AsyncSession, couple: Couple, user: User) -> Itinerary:
    itinerary = Itinerary(couple_id=couple.id, planner_user_id=user.id, title="Saturday")
    session.add(itinerary)
    await session.commit()
    return itinerary


async def test_idea_defaults_and_enum_checks(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    couple = await make_couple(db_session)
    idea = await make_idea(db_session, couple, user)
    await db_session.refresh(idea)
    assert (idea.tags, idea.place_ids, idea.generation) == ([], [], {})
    assert (idea.currency, idea.status) == ("USD", "active")
    assert (idea.grounded, idea.is_surprise) == (False, False)
    assert idea.venue is None
    for bad in (
        {"source": "dream"},
        {"visibility": "public"},
        {"status": "deleted"},
        {"best_time": "dawn"},
        {"indoor_outdoor": "porch"},
    ):
        await expect_violation(db_session, new_idea(couple, user, **bad))


async def test_profile_suggestion_defaults_status_check_and_cascade(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session)
    couple = await make_couple(db_session)
    profile = await make_profile(db_session, couple)
    suggestion = ProfileSuggestion(profile_id=profile.id, author_user_id=user.id)
    db_session.add(suggestion)
    await db_session.commit()
    await db_session.refresh(suggestion)
    assert (suggestion.changes, suggestion.status, suggestion.decided_at) == ({}, "pending", None)
    await expect_violation(
        db_session,
        ProfileSuggestion(profile_id=profile.id, author_user_id=user.id, status="maybe"),
    )
    await db_session.execute(
        delete(PartnerProfile)
        .where(PartnerProfile.id == profile.id)
        .execution_options(synchronize_session=False)
    )
    await db_session.commit()
    assert await count(db_session, ProfileSuggestion) == 0


async def test_one_vote_per_user_per_idea(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    couple = await make_couple(db_session)
    idea = await make_idea(db_session, couple, user)
    db_session.add(Vote(idea_id=idea.id, user_id=user.id, value="yes"))
    await db_session.commit()
    await expect_violation(db_session, Vote(idea_id=idea.id, user_id=user.id, value="no"))
    other = await make_user(db_session, "bo@example.com")
    await expect_violation(db_session, Vote(idea_id=idea.id, user_id=other.id, value="nope"))


async def test_itinerary_end_must_not_precede_start(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    couple = await make_couple(db_session)
    kwargs = {"couple_id": couple.id, "planner_user_id": user.id, "title": "t"}
    later = NOW + dt.timedelta(hours=2)
    await expect_violation(db_session, Itinerary(**kwargs, starts_at=later, ends_at=NOW))
    db_session.add_all(
        [
            Itinerary(**kwargs, starts_at=NOW, ends_at=later),
            Itinerary(**kwargs, starts_at=NOW),
            Itinerary(**kwargs, ends_at=NOW),
        ]
    )
    await db_session.commit()
    await expect_violation(db_session, Itinerary(**kwargs, status="paused"))


async def test_stop_positions_are_unique_but_can_be_swapped_in_one_transaction(
    db_session: AsyncSession,
) -> None:
    user = await make_user(db_session)
    itinerary = await make_itinerary(db_session, await make_couple(db_session), user)
    first = Stop(itinerary_id=itinerary.id, position=1, title="A")
    second = Stop(itinerary_id=itinerary.id, position=2, title="B")
    db_session.add_all([first, second])
    await db_session.commit()
    assert (first.evergreen, first.reveal_policy, first.warnings) == (False, "with_itinerary", [])
    await expect_violation(
        db_session, Stop(itinerary_id=itinerary.id, position=3, title="D", reveal_policy="never")
    )
    # Deferred: the swap passes through a duplicate and is checked at commit.
    first.position, second.position = 2, 1
    await db_session.commit()
    # A real duplicate is only caught at commit, not at flush.
    db_session.add(Stop(itinerary_id=itinerary.id, position=1, title="C"))
    await db_session.flush()
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_one_travel_leg_leaves_each_stop(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    itinerary = await make_itinerary(db_session, await make_couple(db_session), user)
    a = Stop(itinerary_id=itinerary.id, position=1, title="A")
    b = Stop(itinerary_id=itinerary.id, position=2, title="B")
    db_session.add_all([a, b])
    await db_session.commit()
    leg = {"itinerary_id": itinerary.id, "from_stop_id": a.id, "to_stop_id": b.id}
    db_session.add(TravelLeg(**leg, mode="walk", duration_minutes=10))
    await db_session.commit()
    await expect_violation(db_session, TravelLeg(**leg, mode="walk", duration_minutes=5))
    await expect_violation(
        db_session,
        TravelLeg(
            itinerary_id=itinerary.id,
            from_stop_id=b.id,
            to_stop_id=a.id,
            mode="teleport",
            duration_minutes=1,
        ),
    )


async def test_plan_session_and_itinerary_point_at_each_other(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    couple = await make_couple(db_session)
    profile = await make_profile(db_session, couple)
    session = PlanSession(
        couple_id=couple.id, planner_user_id=user.id, recipient_profile_id=profile.id
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    assert (session.status, session.inputs, session.options) == ("open", {}, [])
    itinerary = Itinerary(
        couple_id=couple.id, planner_user_id=user.id, title="t", plan_session_id=session.id
    )
    db_session.add(itinerary)
    await db_session.commit()
    session.itinerary_id = itinerary.id
    await db_session.commit()
    # Deleting either side only nulls the other's pointer.
    await db_session.execute(
        delete(Itinerary)
        .where(Itinerary.id == itinerary.id)
        .execution_options(synchronize_session=False)
    )
    await db_session.commit()
    await db_session.refresh(session)
    assert session.itinerary_id is None
    await expect_violation(
        db_session,
        PlanSession(
            couple_id=couple.id,
            planner_user_id=user.id,
            recipient_profile_id=profile.id,
            status="bogus",
        ),
    )


async def test_feedback_ranges_and_uniqueness(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    couple = await make_couple(db_session)
    profile = await make_profile(db_session, couple)
    itinerary = await make_itinerary(db_session, couple, user)
    for stars in (0, 6):
        await expect_violation(
            db_session,
            Rating(itinerary_id=itinerary.id, user_id=user.id, stars=stars, would_again=True),
        )
    rating = Rating(itinerary_id=itinerary.id, user_id=user.id, stars=5, would_again=True)
    db_session.add(rating)
    await db_session.commit()
    await db_session.refresh(rating)
    assert rating.stop_thumbs == {}
    await expect_violation(
        db_session, Rating(itinerary_id=itinerary.id, user_id=user.id, stars=3, would_again=False)
    )
    await expect_violation(
        db_session, PlannerRead(itinerary_id=itinerary.id, planner_user_id=user.id, enjoyment=6)
    )
    db_session.add(PlannerRead(itinerary_id=itinerary.id, planner_user_id=user.id, enjoyment=4))
    db_session.add(TasteSummary(couple_id=couple.id, about_profile_id=profile.id))
    await db_session.commit()
    await expect_violation(
        db_session, PlannerRead(itinerary_id=itinerary.id, planner_user_id=user.id, enjoyment=2)
    )
    await expect_violation(
        db_session, TasteSummary(couple_id=couple.id, about_profile_id=profile.id)
    )


async def test_chat_thread_scope_rules(db_session: AsyncSession) -> None:
    ann = await make_user(db_session)
    bo = await make_user(db_session, "bo@example.com")
    couple = await make_couple(db_session)
    db_session.add_all(
        [
            ChatThread(couple_id=couple.id, scope="shared"),
            ChatThread(couple_id=couple.id, scope="private", owner_user_id=ann.id),
            ChatThread(couple_id=couple.id, scope="private", owner_user_id=bo.id),
        ]
    )
    await db_session.commit()
    # A second shared thread, a second private thread for one owner, a private thread without an
    # owner and a shared thread with one are all refused.
    await expect_violation(db_session, ChatThread(couple_id=couple.id, scope="shared"))
    await expect_violation(
        db_session, ChatThread(couple_id=couple.id, scope="private", owner_user_id=ann.id)
    )
    other = await make_couple(db_session)
    await expect_violation(db_session, ChatThread(couple_id=other.id, scope="private"))
    await expect_violation(
        db_session, ChatThread(couple_id=other.id, scope="shared", owner_user_id=ann.id)
    )
    await expect_violation(db_session, ChatThread(couple_id=other.id, scope="team"))


async def test_chat_messages_cascade_with_their_thread(db_session: AsyncSession) -> None:
    couple = await make_couple(db_session)
    thread = ChatThread(couple_id=couple.id, scope="shared")
    db_session.add(thread)
    await db_session.commit()
    message = ChatMessage(thread_id=thread.id, role="user", content="hi")
    db_session.add(message)
    await db_session.commit()
    await db_session.refresh(message)
    assert (message.actions, message.tool_trace) == ([], [])
    await expect_violation(db_session, ChatMessage(thread_id=thread.id, role="bot", content="x"))
    await db_session.execute(
        delete(ChatThread)
        .where(ChatThread.id == thread.id)
        .execution_options(synchronize_session=False)
    )
    await db_session.commit()
    assert await count(db_session, ChatMessage) == 0


async def test_notification_dedupe_key_is_unique_but_optional(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    note = {"user_id": user.id, "kind": "k", "title": "t", "body": "b"}
    db_session.add_all([Notification(**note), Notification(**note)])  # NULL keys do not collide
    db_session.add(Notification(**note, dedupe_key="once"))
    await db_session.commit()
    await expect_violation(db_session, Notification(**note, dedupe_key="once"))
    stored = (await db_session.execute(select(Notification).limit(1))).scalar_one()
    await db_session.refresh(stored)
    assert (stored.data, stored.deliver_after is not None) == ({}, True)


async def test_provider_cache_has_a_composite_key(db_session: AsyncSession) -> None:
    db_session.add(ProviderCache(provider="places", key="a" * 64, payload={}, expires_at=NOW))
    db_session.add(ProviderCache(provider="weather", key="a" * 64, payload={}, expires_at=NOW))
    await db_session.commit()
    await expect_violation(
        db_session, ProviderCache(provider="places", key="a" * 64, payload={}, expires_at=NOW)
    )


async def test_ai_usage_survives_deleting_its_couple(db_session: AsyncSession) -> None:
    couple = await make_couple(db_session)
    db_session.add(
        AIUsage(
            couple_id=couple.id,
            feature="chat",
            model="m",
            input_tokens=1,
            output_tokens=2,
            cache_read_tokens=0,
            cache_write_tokens=0,
            tool_calls=0,
            est_cost_usd=Decimal("0.000123"),
            ok=True,
        )
    )
    await db_session.commit()
    await db_session.execute(
        delete(Couple).where(Couple.id == couple.id).execution_options(synchronize_session=False)
    )
    await db_session.commit()
    row = (await db_session.execute(select(AIUsage))).scalar_one()
    assert row.couple_id is None
    assert row.est_cost_usd == Decimal("0.000123")


async def test_deleting_a_couple_cascades_to_the_v1_tables(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    couple = await make_couple(db_session)
    profile = await make_profile(db_session, couple)
    idea = await make_idea(db_session, couple, user)
    itinerary = await make_itinerary(db_session, couple, user)
    db_session.add_all(
        [
            Vote(idea_id=idea.id, user_id=user.id, value="maybe"),
            Stop(itinerary_id=itinerary.id, position=1, title="A"),
            ChecklistItem(itinerary_id=itinerary.id, position=1, text="Book", kind="reserve"),
            Rating(itinerary_id=itinerary.id, user_id=user.id, stars=4, would_again=True),
            TasteSummary(couple_id=couple.id, about_profile_id=profile.id),
            ChatThread(couple_id=couple.id, scope="shared"),
            Notification(user_id=user.id, couple_id=couple.id, kind="k", title="t", body="b"),
        ]
    )
    await db_session.commit()
    await db_session.execute(
        delete(Couple).where(Couple.id == couple.id).execution_options(synchronize_session=False)
    )
    await db_session.commit()
    for model in (
        Idea,
        Vote,
        Itinerary,
        Stop,
        ChecklistItem,
        Rating,
        TasteSummary,
        ChatThread,
        Notification,
        PartnerProfile,
    ):
        assert await count(db_session, model) == 0, model.__name__
    assert await db_session.scalar(select(func.count()).select_from(User)) == 1
