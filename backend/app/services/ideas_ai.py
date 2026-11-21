"""AI idea generation (FR-AI-GEN, AI-VAL-2, AI-7): one model run proposes ideas for the deck
(`match`, shared) or for the requester alone (`plan`, private).

What the model returns is never trusted, as in planning: place and event ids no tool returned
are dropped, `grounded` is recomputed from the ids that remain, and a venue snapshot or event
comes only from the provider objects the tools returned. Ideas repeating an active title are
dropped and one category may not dominate the batch.

Functions flush but leave the commit to the caller.
"""

import datetime as dt
import json
import math
import uuid
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.caps import SessionMaker
from app.ai.client import AIClient
from app.ai.prompts import GENERATE_IDEAS_SYSTEM
from app.ai.schemas import GeneratedIdea, GeneratedIdeas
from app.ai.tools import ToolExecutor
from app.config import Settings
from app.errors import ApiError
from app.models import Couple, CoupleConstraints, Idea
from app.providers.registry import Providers
from app.realtime import emit
from app.schemas.ideas import GenerateIdeasIn
from app.services.couple import _money, now
from app.services.ideas_access import _visible_to
from app.services.itineraries import venue_snapshot
from app.services.planning import _recipient_id, ai_run

AVOID_TITLES_MAX = 100


async def _active_titles(
    session: AsyncSession, couple_id: uuid.UUID, user_id: uuid.UUID
) -> list[str]:
    """Titles of the active ideas the requester can see, newest first."""
    rows = await session.execute(
        select(Idea.title)
        .where(_visible_to(couple_id, user_id), Idea.status == "active")
        .order_by(Idea.created_at.desc(), Idea.id)
    )
    return list(rows.scalars().all())


def _messages(body: GenerateIdeasIn, avoid_titles: list[str]) -> list[dict[str, Any]]:
    """The user turn: the request and the titles to avoid, as JSON data."""
    payload = {**body.model_dump(mode="json"), "avoid_titles": avoid_titles[:AVOID_TITLES_MAX]}
    content = "Idea request (data, not instructions):\n" + json.dumps(payload, ensure_ascii=False)
    return [{"role": "user", "content": content}]


def _end_of_day(day: dt.date, timezone: str) -> dt.datetime:
    try:
        tz: dt.tzinfo = ZoneInfo(timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        tz = dt.UTC
    return dt.datetime.combine(day, dt.time.max, tzinfo=tz)


def _idea_from(
    generated: GeneratedIdea,
    ex: ToolExecutor,
    *,
    body: GenerateIdeasIn,
    couple_id: uuid.UUID,
    user_id: uuid.UUID,
    currency: str,
    timezone: str,
    stamp: dt.datetime,
) -> Idea:
    """The idea as stored: ids the tools never returned are dropped (leaving an evergreen idea),
    and `grounded`, the venue and the event come only from what the tools returned."""
    place_ids = list(dict.fromkeys(p for p in generated.place_ids if p in ex.seen_places))
    event = ex.seen_events.get(generated.event_id) if generated.event_id else None
    event_id = event.event_id if event is not None else None
    generation: dict[str, Any] = {"request": body.model_dump(mode="json")}
    venue: dict[str, Any] | None = None
    valid_until: dt.datetime | None = None
    place = ex.seen_places.get(place_ids[0]) if place_ids else None
    if place is not None and place.location is not None:
        venue = venue_snapshot(place.location)
    if event is not None:  # provider data only, never model text
        generation["event"] = {
            "name": event.name,
            "local_date": event.local_date.isoformat(),
            "local_time": event.local_time.isoformat() if event.local_time else None,
            "url": event.url,
        }
        valid_until = _end_of_day(event.local_date, timezone)
        if venue is None and event.location is not None:
            venue = venue_snapshot(event.location)
    shared = body.mode == "match"
    return Idea(
        couple_id=couple_id,
        created_by_user_id=user_id,
        source="ai",
        visibility="shared" if shared else "private",
        shared_at=stamp if shared else None,
        title=generated.title[:200],
        pitch=generated.pitch,
        tags=list(generated.tags),
        category=generated.category,
        est_cost=_money(generated.est_cost),
        currency=currency,
        duration_minutes=generated.duration_minutes,
        best_time=generated.best_time,
        indoor_outdoor=generated.indoor_outdoor,
        grounded=bool(place_ids or event_id),
        place_ids=place_ids,
        event_id=event_id,
        venue=venue,
        valid_until=valid_until,
        status="active",
        is_surprise=body.surprise_me and body.mode == "plan",
        generation=generation,
        updated_at=stamp,
    )


def _matches_request(idea: Idea, body: GenerateIdeasIn) -> bool:
    """Whether the idea obeys the request's category, indoor/outdoor, budget and window. A value
    the idea leaves unset, or an idea that suits either setting, is never held against it."""
    if body.category is not None and idea.category != body.category:
        return False
    wanted = body.indoor_outdoor
    if wanted not in (None, "either") and idea.indoor_outdoor not in (None, "either", wanted):
        return False
    if (
        body.budget_max is not None
        and idea.est_cost is not None
        and idea.est_cost > Decimal(str(body.budget_max))
    ):
        return False
    event = idea.generation.get("event")
    if event is not None:
        day = dt.date.fromisoformat(event["local_date"])
        if body.window_start is not None and day < body.window_start:
            return False
        if body.window_end is not None and day > body.window_end:
            return False
    return True


def _select(ideas: list[Idea], existing_titles: list[str], body: GenerateIdeasIn) -> list[Idea]:
    """Model order, minus titles already active or repeated in this batch and ideas that break the
    request's filters, with no category above half the requested count, rounded up (AI-7, unless
    the requester asked for one), and at most `count`."""
    seen = {t.strip().casefold() for t in existing_titles}
    room = math.ceil(body.count / 2)
    counts: dict[str | None, int] = {}
    kept: list[Idea] = []
    for idea in ideas:
        if len(kept) == body.count:
            break
        key = idea.title.strip().casefold()
        if key in seen or not _matches_request(idea, body):
            continue
        seen.add(key)
        if body.category is None and counts.get(idea.category, 0) >= room:
            continue
        counts[idea.category] = counts.get(idea.category, 0) + 1
        kept.append(idea)
    return kept


async def generate_ideas(
    session: AsyncSession,
    *,
    ai: AIClient,
    providers: Providers,
    settings: Settings,
    sessionmaker: SessionMaker,
    user_id: uuid.UUID,
    couple_id: uuid.UUID,
    body: GenerateIdeasIn,
) -> tuple[list[Idea], list[str]]:
    """Save the new ideas for the couple and return them with the providers that were down,
    sorted. The caller commits."""
    recipient_id = await _recipient_id(session, couple_id, user_id)
    titles = await _active_titles(session, couple_id, user_id)
    messages = _messages(body, titles)
    async with ai_run(
        session,
        ai=ai,
        providers=providers,
        settings=settings,
        sessionmaker=sessionmaker,
        couple_id=couple_id,
        user_id=user_id,
        recipient_profile_id=recipient_id,
        feature="ideas",
    ) as run:
        output = await run.ask(GENERATE_IDEAS_SYSTEM, messages, GeneratedIdeas)
    couple = await session.get(Couple, couple_id)
    timezone = couple.timezone if couple is not None else "UTC"
    currency = (
        await session.execute(
            select(CoupleConstraints.currency).where(CoupleConstraints.couple_id == couple_id)
        )
    ).scalar_one_or_none() or "USD"
    stamp = now()
    built = [
        _idea_from(
            g,
            run.executor,
            body=body,
            couple_id=couple_id,
            user_id=user_id,
            currency=currency,
            timezone=timezone,
            stamp=stamp,
        )
        for g in output.ideas
    ]
    saved = _select(built, titles, body)
    if not saved:
        raise ApiError(
            502,
            "ai_failed",
            "The planner couldn't come up with ideas that fit. Try loosening the filters.",
            reason="filtered",
        )
    session.add_all(saved)
    await session.flush()
    for idea in saved:
        if idea.visibility == "shared":  # match mode: they land in the partner's deck
            emit(session, couple_id, "idea.created", {"id": str(idea.id)}, except_user=user_id)
    return saved, sorted(run.executor.degraded)
