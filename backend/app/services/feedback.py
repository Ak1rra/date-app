"""After-date feedback (M7): completing a date, ratings, the planner's read, past dates.

A scheduled date completes by a job at its end time. Each partner rates it; the partner's
rating stays hidden until both have rated or a week has passed since the date ended. That
visibility is computed on read, never stored.
"""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ApiError
from app.models import CoupleMember, Itinerary, Job, PlannerRead, Rating, Stop
from app.realtime import emit, emit_itinerary
from app.schemas.feedback import (
    PastDateOut,
    PlannerReadIn,
    PlannerReadOut,
    RatingIn,
    RatingOut,
    RatingsOut,
)
from app.services import taste
from app.services.couple import now
from app.services.itinerary_access import visible_itineraries
from app.services.notify import notify
from app.worker.jobs import enqueue

ITINERARY_COMPLETE = "itinerary.complete"
RATINGS_REVEAL = "ratings.reveal"

DEFAULT_DURATION = dt.timedelta(hours=3)
REVEAL_AFTER = dt.timedelta(days=7)
PAST_DATES_LIMIT = 100


# --- Completion


def end_of(itinerary: Itinerary) -> dt.datetime | None:
    """When the date ends: its end time, else three hours after it starts; None if unscheduled."""
    if itinerary.ends_at is not None:
        return itinerary.ends_at
    if itinerary.starts_at is not None:
        return itinerary.starts_at + DEFAULT_DURATION
    return None


async def schedule_completion(session: AsyncSession, itinerary: Itinerary) -> None:
    """Queue the job that completes the date once it has ended. A reschedule changes the end
    time, so it gets a new job and the old one no-ops. Moving back to an earlier end time
    re-arms that time's job if it already ran (as a no-op) and is finished. The caller
    commits."""
    end = end_of(itinerary)
    if end is None:
        return
    dedupe_key = f"complete:{itinerary.id}:{end.isoformat()}"
    await enqueue(
        session,
        ITINERARY_COMPLETE,
        {"itinerary_id": str(itinerary.id), "end": end.isoformat()},
        run_at=end,
        dedupe_key=dedupe_key,
    )
    await session.execute(
        update(Job)
        .where(Job.dedupe_key == dedupe_key, Job.status.in_(("done", "failed")))
        .values(
            status="queued",
            run_at=end,
            attempts=0,
            locked_at=None,
            locked_by=None,
            last_error=None,
            finished_at=None,
        ),
        execution_options={"synchronize_session": False},
    )


async def _member_ids(session: AsyncSession, couple_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await session.execute(
                select(CoupleMember.user_id).where(
                    CoupleMember.couple_id == couple_id, CoupleMember.left_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )


async def complete(
    session: AsyncSession, itinerary_id: uuid.UUID, end: dt.datetime, settings: Settings
) -> None:
    """Mark a scheduled date completed, prompt both members to rate it and queue the rating
    reveal. A no-op unless the date is still scheduled and still ends at `end`."""
    itinerary = await session.get(Itinerary, itinerary_id, with_for_update=True)
    if itinerary is None or itinerary.status != "scheduled" or end_of(itinerary) != end:
        return
    moment = now()
    itinerary.status = "completed"
    itinerary.updated_at = moment
    if itinerary.is_surprise and itinerary.revealed_at is None:
        itinerary.revealed_at = moment
    await session.flush()
    await enqueue(
        session,
        RATINGS_REVEAL,
        {"itinerary_id": str(itinerary.id)},
        run_at=end + REVEAL_AFTER,
        dedupe_key=f"ratings-reveal:{itinerary.id}",
    )
    for user_id in await _member_ids(session, itinerary.couple_id):
        await notify(
            session,
            user_id,
            "rate_prompt",
            "How was your date? ⭐",
            f"Tap to rate {itinerary.title}.",
            {"url": f"/itineraries/{itinerary.id}?rate=1"},
            couple_id=itinerary.couple_id,
            dedupe_key=f"rate:{itinerary.id}:{user_id}",
            settings=settings,
        )
    emit_itinerary(session, itinerary, "date.completed")


async def reveal_ratings(session: AsyncSession, itinerary_id: uuid.UUID) -> None:
    """Tell the couple the waiting period is over. Visibility itself is computed on read.
    Nothing to announce if both had already rated: the rating that completed the set did."""
    itinerary = await session.get(Itinerary, itinerary_id)
    if itinerary is None or itinerary.status != "completed":
        return
    members = set(await _member_ids(session, itinerary.couple_id))
    if _both_rated(members, await _ratings(session, itinerary.id)):
        return
    emit(session, itinerary.couple_id, "rating.revealed", {"itinerary_id": str(itinerary.id)})


# --- Ratings


def _require_completed(itinerary: Itinerary) -> None:
    if itinerary.status != "completed":
        raise ApiError(409, "not_completed", "This date hasn't happened yet.")


def _revealed(itinerary: Itinerary, both_rated: bool, moment: dt.datetime) -> bool:
    end = end_of(itinerary)
    return both_rated or (end is not None and moment >= end + REVEAL_AFTER)


def _both_rated(members: set[uuid.UUID], ratings: list[Rating]) -> bool:
    return len(members) > 1 and members <= {rating.user_id for rating in ratings}


def _partner_rating(
    ratings: list[Rating], members: set[uuid.UUID], viewer_id: uuid.UUID
) -> Rating | None:
    """The rating of the current member who isn't the viewer. A departed member's rating is
    never shown to anyone but its author."""
    return next((r for r in ratings if r.user_id != viewer_id and r.user_id in members), None)


async def _ratings(session: AsyncSession, itinerary_id: uuid.UUID) -> list[Rating]:
    return list(
        (
            await session.execute(
                select(Rating)
                .where(Rating.itinerary_id == itinerary_id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def put_rating(
    session: AsyncSession, itinerary: Itinerary, user_id: uuid.UUID, body: RatingIn
) -> Rating:
    """Create or replace the caller's rating. The caller commits. Emits `rating.revealed` only
    when this rating is the one that completes the set."""
    await session.refresh(itinerary, with_for_update=True)  # serialises concurrent ratings
    _require_completed(itinerary)
    stop_ids = set(
        (await session.execute(select(Stop.id).where(Stop.itinerary_id == itinerary.id)))
        .scalars()
        .all()
    )
    if not set(body.stop_thumbs) <= stop_ids:
        raise ApiError(422, "unknown_stop", "One of those stops isn't part of this date.")
    had_rating = (
        await session.execute(
            select(Rating.id).where(Rating.itinerary_id == itinerary.id, Rating.user_id == user_id)
        )
    ).scalar_one_or_none()
    values: dict[str, Any] = {
        "stars": body.stars,
        "would_again": body.would_again,
        "comment": body.comment,
        "stop_thumbs": {str(stop_id): thumb for stop_id, thumb in body.stop_thumbs.items()},
        "updated_at": now(),
    }
    await session.execute(
        pg_insert(Rating)
        .values(itinerary_id=itinerary.id, user_id=user_id, **values)
        .on_conflict_do_update(index_elements=[Rating.itinerary_id, Rating.user_id], set_=values)
    )
    await taste.schedule_recompute(session, itinerary.couple_id, user_ids=[user_id])
    ratings = await _ratings(session, itinerary.id)
    if had_rating is None and _both_rated(
        set(await _member_ids(session, itinerary.couple_id)), ratings
    ):
        emit(session, itinerary.couple_id, "rating.revealed", {"itinerary_id": str(itinerary.id)})
    return next(rating for rating in ratings if rating.user_id == user_id)


async def put_planner_read(
    session: AsyncSession, itinerary: Itinerary, user_id: uuid.UUID, body: PlannerReadIn
) -> PlannerRead:
    """Create or replace the planner's read. The caller checks they are the planner and
    commits."""
    _require_completed(itinerary)
    values: dict[str, Any] = {
        "enjoyment": body.enjoyment,
        "highlights": body.highlights,
        "misses": body.misses,
        "updated_at": now(),
    }
    await session.execute(
        pg_insert(PlannerRead)
        .values(itinerary_id=itinerary.id, planner_user_id=user_id, **values)
        .on_conflict_do_update(
            index_elements=[PlannerRead.itinerary_id, PlannerRead.planner_user_id], set_=values
        )
    )
    # The read describes the partner, so it is the partner's taste that changes.
    partners = [m for m in await _member_ids(session, itinerary.couple_id) if m != user_id]
    await taste.schedule_recompute(session, itinerary.couple_id, user_ids=partners)
    return (
        await session.execute(
            select(PlannerRead)
            .where(PlannerRead.itinerary_id == itinerary.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def get_ratings(
    session: AsyncSession, itinerary: Itinerary, viewer_id: uuid.UUID
) -> RatingsOut:
    ratings = await _ratings(session, itinerary.id)
    members = set(await _member_ids(session, itinerary.couple_id))
    revealed = _revealed(itinerary, _both_rated(members, ratings), now())
    mine = next((r for r in ratings if r.user_id == viewer_id), None)
    partner = _partner_rating(ratings, members, viewer_id) if revealed else None
    planner_read: PlannerRead | None = None
    if itinerary.planner_user_id == viewer_id:
        planner_read = (
            await session.execute(
                select(PlannerRead).where(PlannerRead.itinerary_id == itinerary.id)
            )
        ).scalar_one_or_none()
    return RatingsOut(
        mine=RatingOut.model_validate(mine) if mine else None,
        partner=RatingOut.model_validate(partner) if partner else None,
        partner_revealed=revealed,
        planner_read=PlannerReadOut.model_validate(planner_read) if planner_read else None,
    )


# --- Past dates


async def past_dates(
    session: AsyncSession, viewer_id: uuid.UUID, couple_id: uuid.UUID
) -> list[PastDateOut]:
    """The viewer's completed dates, newest first, each with the ratings they may see."""
    itineraries = list(
        (
            await session.execute(
                visible_itineraries(session, viewer_id, couple_id)
                .where(Itinerary.status == "completed")
                .order_by(Itinerary.starts_at.desc().nulls_last(), Itinerary.id)
                .limit(PAST_DATES_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    if not itineraries:
        return []
    ids = [itinerary.id for itinerary in itineraries]
    members = set(await _member_ids(session, couple_id))
    by_itinerary: dict[uuid.UUID, list[Rating]] = {}
    for rating in (
        (await session.execute(select(Rating).where(Rating.itinerary_id.in_(ids)))).scalars().all()
    ):
        by_itinerary.setdefault(rating.itinerary_id, []).append(rating)
    reads = {
        read.itinerary_id: read
        for read in (
            await session.execute(select(PlannerRead).where(PlannerRead.itinerary_id.in_(ids)))
        )
        .scalars()
        .all()
    }
    moment = now()
    out: list[PastDateOut] = []
    for itinerary in itineraries:
        ratings = by_itinerary.get(itinerary.id, [])
        revealed = _revealed(itinerary, _both_rated(members, ratings), moment)
        mine = next((r for r in ratings if r.user_id == viewer_id), None)
        partner = _partner_rating(ratings, members, viewer_id) if revealed else None
        read = reads.get(itinerary.id) if itinerary.planner_user_id == viewer_id else None
        out.append(
            PastDateOut(
                id=itinerary.id,
                title=itinerary.title,
                starts_at=itinerary.starts_at,
                ends_at=itinerary.ends_at,
                is_surprise=itinerary.is_surprise,
                planner_user_id=itinerary.planner_user_id,
                mine=RatingOut.model_validate(mine) if mine else None,
                partner=RatingOut.model_validate(partner) if partner else None,
                planner_read=PlannerReadOut.model_validate(read) if read else None,
            )
        )
    return out
