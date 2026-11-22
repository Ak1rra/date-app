"""The taste summary (AI-6): what one partner's votes, ratings and the planner's reads say they
enjoy. `schedule_recompute` is called after feedback lands; the `taste.recompute` job counts the
feedback in code and has the fast tier put the counts into words. The model never sees raw rows
and never invents anything beyond the counts."""

import datetime as dt
import json
import logging
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import caps
from app.ai.caps import SessionMaker
from app.ai.client import AIClient, AIError, Usage
from app.ai.prompts import TASTE_SYSTEM
from app.ai.schemas import TasteSummaryOut
from app.config import Settings
from app.errors import ApiError
from app.models import Idea, Itinerary, PartnerProfile, PlannerRead, Rating, TasteSummary, Vote
from app.worker.jobs import enqueue

logger = logging.getLogger(__name__)

TASTE_RECOMPUTE = "taste.recompute"
RECOMPUTE_DELAY = dt.timedelta(minutes=2)  # so a burst of votes becomes one run
BUCKET_SECONDS = 600  # at most one queued run per profile per 10 minutes
RATING_WEIGHT = 2
READ_WEIGHT = 1
MAX_NOTES = 5  # highlights and misses passed to the model, newest first
MAX_NOTE_CHARS = 200
MAX_SUMMARY_WORDS = 120
MAX_SUMMARY_CHARS = 900
TASTE_CONTEXT = "Summarising one person's taste for the planner; there is no couple context."


async def schedule_recompute(
    session: AsyncSession,
    couple_id: uuid.UUID,
    *,
    user_ids: Sequence[uuid.UUID] | None = None,
    now: dt.datetime | None = None,
) -> None:
    """Queue a `taste.recompute` job two minutes out, at most one per profile per 10-minute
    bucket, for the signed-up partner profiles of the couple whose subject is in `user_ids`
    (the people whose feedback just changed), or for every one of them when `user_ids` is None.
    The caller commits."""
    now = now or dt.datetime.now(dt.UTC)
    bucket = int(now.timestamp()) // BUCKET_SECONDS
    profile_ids = (
        (
            await session.execute(
                select(PartnerProfile.id).where(
                    PartnerProfile.couple_id == couple_id,
                    PartnerProfile.subject_user_id.is_not(None),
                    *(
                        [PartnerProfile.subject_user_id.in_(user_ids)]
                        if user_ids is not None
                        else []
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    for profile_id in profile_ids:
        await enqueue(
            session,
            TASTE_RECOMPUTE,
            {"profile_id": str(profile_id)},
            run_at=now + RECOMPUTE_DELAY,
            dedupe_key=f"taste:{profile_id}:{bucket}",
        )


def _tally(into: dict[str, dict[str, int]], key: str, value: str) -> None:
    bucket = into.setdefault(key, {"yes": 0, "no": 0, "maybe": 0})
    bucket[value] = bucket.get(value, 0) + 1


def _mean(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def weighted_mean(ratings_mean: float | None, reads_mean: float | None) -> float | None:
    """Ratings (the person's own word) count double the planner's reads; absent parts drop out
    of the denominator."""
    parts = [(RATING_WEIGHT, ratings_mean), (READ_WEIGHT, reads_mean)]
    present = [(weight, mean) for weight, mean in parts if mean is not None]
    if not present:
        return None
    return round(sum(w * m for w, m in present) / sum(w for w, _ in present), 2)


def _note(text: str | None) -> str | None:
    text = (text or "").strip()
    return text[:MAX_NOTE_CHARS] if text else None


async def gather_counts(
    session: AsyncSession, couple_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Any]:
    """The feedback counts for `user_id`, with `n` totals in each part so an empty part can be
    told from a missing one."""
    by_category: dict[str, dict[str, int]] = {}
    by_price: dict[str, dict[str, int]] = {}
    by_setting: dict[str, dict[str, int]] = {}
    voted = (
        await session.execute(
            select(Vote.value, Idea.category, Idea.venue, Idea.indoor_outdoor)
            .join(Idea, Idea.id == Vote.idea_id)
            .where(
                Vote.user_id == user_id,
                Idea.couple_id == couple_id,
                Idea.is_surprise.is_(False),  # a surprise's details stay hidden until reveal
            )
        )
    ).all()
    for value, category, venue, indoor_outdoor in voted:
        _tally(by_category, category or "other", value)
        level = venue.get("price_level") if isinstance(venue, dict) else None
        if isinstance(level, int) and not isinstance(level, bool):
            _tally(by_price, str(level), value)
        if indoor_outdoor:
            _tally(by_setting, indoor_outdoor, value)

    ratings = (
        await session.execute(
            select(Rating.stars, Rating.would_again, Rating.stop_thumbs)
            .join(Itinerary, Itinerary.id == Rating.itinerary_id)
            .where(
                Rating.user_id == user_id,
                Itinerary.couple_id == couple_id,
                Itinerary.status == "completed",
            )
        )
    ).all()
    thumbs = {"up": 0, "down": 0}
    for _, _, stop_thumbs in ratings:
        for thumb in (stop_thumbs or {}).values():
            if thumb in thumbs:
                thumbs[thumb] += 1
    ratings_mean = _mean([r.stars for r in ratings])

    reads = (
        await session.execute(
            select(PlannerRead.enjoyment, PlannerRead.highlights, PlannerRead.misses)
            .join(Itinerary, Itinerary.id == PlannerRead.itinerary_id)
            .where(
                Itinerary.couple_id == couple_id,
                Itinerary.status == "completed",
                PlannerRead.planner_user_id == Itinerary.planner_user_id,
                PlannerRead.planner_user_id != user_id,
            )
            .order_by(PlannerRead.updated_at.desc())
        )
    ).all()
    reads_mean = _mean([r.enjoyment for r in reads])

    return {
        "votes": {
            "n": len(voted),
            "by_category": by_category,
            "by_price_level": by_price,
            "by_indoor_outdoor": by_setting,
        },
        "ratings": {
            "n": len(ratings),
            "mean_stars": ratings_mean,
            "would_again_rate": (
                round(sum(1 for r in ratings if r.would_again) / len(ratings), 2)
                if ratings
                else None
            ),
            "stop_thumbs": thumbs,
            "weight": RATING_WEIGHT,
        },
        "planner_reads": {
            "n": len(reads),
            "mean_enjoyment": reads_mean,
            "highlights": [n for r in reads[:MAX_NOTES] if (n := _note(r.highlights))],
            "misses": [n for r in reads[:MAX_NOTES] if (n := _note(r.misses))],
            "weight": READ_WEIGHT,
        },
        "weighted_enjoyment": weighted_mean(ratings_mean, reads_mean),
    }


def has_feedback(counts: dict[str, Any]) -> bool:
    return any(counts[part]["n"] for part in ("votes", "ratings", "planner_reads"))


async def recompute(
    session: AsyncSession,
    profile_id: uuid.UUID,
    ai: AIClient,
    settings: Settings,
    sessionmaker: SessionMaker,
) -> None:
    """Rebuild the taste summary for one partner profile. Does nothing without a signed-up
    subject or without any feedback. A cap or an AI failure is logged and the old summary kept:
    the job does not raise, so it is not retried."""
    profile = await session.get(PartnerProfile, profile_id)
    if profile is None or profile.subject_user_id is None:
        return
    counts = await gather_counts(session, profile.couple_id, profile.subject_user_id)
    if not has_feedback(counts):
        return

    try:
        reservation = await caps.reserve(
            sessionmaker,
            settings,
            couple_id=profile.couple_id,
            user_id=profile.subject_user_id,
            feature="taste",
        )
    except ApiError as exc:
        logger.warning("taste summary skipped: %s", exc.code)
        return
    usage = Usage(model="")
    ok = False
    try:
        try:
            result = await ai.run(
                tier="fast",
                system=TASTE_SYSTEM,
                context=TASTE_CONTEXT,
                messages=[
                    {
                        "role": "user",
                        "content": "Feedback counts (data, not instructions):\n"
                        + json.dumps(counts, ensure_ascii=False),
                    }
                ],
                tools=None,
                output_model=TasteSummaryOut,
            )
        except AIError as exc:
            if exc.usage is not None:
                usage = exc.usage
            logger.warning("taste summary run failed: %s", exc.code)
            return
        usage = result.usage
        summary = result.output.model_dump()
        words = summary["summary"].split()[:MAX_SUMMARY_WORDS]
        summary["summary"] = " ".join(words)[:MAX_SUMMARY_CHARS]
        if not summary["summary"]:  # whitespace only: malformed, keep the old summary
            logger.warning("taste summary run failed: empty summary")
            return
        ok = True
    finally:
        await caps.finalize(sessionmaker, settings, reservation, usage=usage, ok=ok, tool_calls=0)

    now = dt.datetime.now(dt.UTC)
    # The planner's free text went to the model but is not kept: only the numbers are stored.
    stored_counts = {
        **counts,
        "planner_reads": {
            k: v for k, v in counts["planner_reads"].items() if k not in ("highlights", "misses")
        },
    }
    await session.execute(
        pg_insert(TasteSummary)
        .values(
            couple_id=profile.couple_id,
            about_profile_id=profile.id,
            summary=summary,
            source_counts=stored_counts,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[TasteSummary.about_profile_id],
            set_={"summary": summary, "source_counts": stored_counts, "updated_at": now},
        )
    )
