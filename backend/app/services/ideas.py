"""Date ideas: my private ideas, the shared deck, votes and the match lists (FR-IDEA-*,
FR-VOTE-*).

Visibility lives in `ideas_access`. Functions flush but leave the commit to the caller.
"""

import math
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ApiError
from app.models import Couple, CoupleMember, Idea, Itinerary, Vote
from app.realtime import emit
from app.schemas.ideas import (
    DeckOut,
    EventOut,
    IdeaIn,
    IdeaListsOut,
    IdeaOut,
    IdeaPatch,
    VenueOut,
    VotesOut,
)
from app.services import taste
from app.services.couple import CENT, not_found, now
from app.services.ideas_access import (
    get_editable_idea,
    get_visible_idea,
    shared_deck_ideas,
    shared_ideas,
    shared_listed_ideas,
)
from app.services.itinerary_access import visible_itineraries
from app.services.notify import notify

DECK_SIZE = 20
UNDO_WINDOW = timedelta(minutes=10)
STALE_AFTER = timedelta(days=30)
EARTH_RADIUS_KM = 6371.0

# The fields a create or a PATCH sets one by one, besides title, pitch and tags (never null).
_OPTIONAL_FIELDS = ("category", "est_cost", "duration_minutes", "best_time", "indoor_outdoor")


async def on_match(
    session: AsyncSession,
    idea: Idea,
    last_voter_user_id: uuid.UUID,
    settings: Settings,
) -> None:
    """Notify the earlier voter when an idea becomes a match, and tell both live."""
    partner = await partner_user_id(session, idea.couple_id, last_voter_user_id)
    if partner is None or idea.is_surprise:  # a surprise idea is hidden from the partner
        return
    emit(session, idea.couple_id, "match.created", {"idea_id": str(idea.id), "title": idea.title})
    await notify(
        session,
        partner,
        "match",
        "It's a match! 💘",
        f"You both said yes to “{idea.title[:80]}”.",
        {"url": "/ideas"},
        couple_id=idea.couple_id,
        dedupe_key=f"match:{idea.id}",
        settings=settings,
    )


async def notify_ideas_added(
    session: AsyncSession,
    couple_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    count: int,
    settings: Settings,
) -> None:
    """Tell the partner that new shared ideas are in the deck: at most one notification an hour
    (the dedupe key is the UTC hour). Never call it for a surprise idea."""
    partner = await partner_user_id(session, couple_id, actor_user_id)
    if partner is None or count < 1:
        return
    hour = now().astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    await notify(
        session,
        partner,
        "ideas_added",
        "New date ideas to swipe 💡",
        f"{count} new {'idea' if count == 1 else 'ideas'} in your deck.",
        {"url": "/ideas"},
        couple_id=couple_id,
        dedupe_key=f"ideas_added:{partner}:{hour.isoformat()}",
        settings=settings,
    )


async def get_couple(session: AsyncSession, couple_id: uuid.UUID) -> Couple:
    couple = (
        await session.execute(select(Couple).where(Couple.id == couple_id))
    ).scalar_one_or_none()
    if couple is None:
        raise not_found()
    return couple


async def partner_user_id(
    session: AsyncSession, couple_id: uuid.UUID, me: uuid.UUID
) -> uuid.UUID | None:
    """The other current member of the couple; None while unpaired."""
    return (
        await session.execute(
            select(CoupleMember.user_id).where(
                CoupleMember.couple_id == couple_id,
                CoupleMember.user_id != me,
                CoupleMember.left_at.is_(None),
            )
        )
    ).scalar_one_or_none()


# --- Output ---------------------------------------------------------------------------------


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _venue_position(venue: dict[str, Any]) -> tuple[float, float] | None:
    """The venue's coordinates, from `lat`/`lng` or a nested `location`."""
    location = venue.get("location")
    place: dict[str, Any] = location if isinstance(location, dict) else venue
    lat, lng = place.get("lat"), place.get("lng")
    if isinstance(lat, int | float) and isinstance(lng, int | float):
        return float(lat), float(lng)
    return None


def _venue_out(venue: dict[str, Any] | None, couple: Couple) -> VenueOut | None:
    if venue is None:
        return None
    distance: float | None = None
    position = _venue_position(venue)
    if position is not None and couple.home_lat is not None and couple.home_lng is not None:
        home = (float(couple.home_lat), float(couple.home_lng))
        distance = round(_haversine_km(*home, *position), 1)
    return VenueOut(
        name=venue.get("name"),
        rating=venue.get("rating"),
        price_level=venue.get("price_level"),
        address=venue.get("address"),
        distance_km=distance,
    )


def _event_out(generation: dict[str, Any]) -> EventOut | None:
    event = generation.get("event")
    if not isinstance(event, dict) or not event.get("name"):
        return None
    return EventOut(
        name=str(event["name"]),
        local_date=event.get("local_date"),
        local_time=event.get("local_time"),
        url=event.get("url"),
    )


def _may_have_changed(venue: dict[str, Any] | None) -> bool:
    """The venue snapshot is older than 30 days (FR-IDEA-4)."""
    fetched = venue.get("fetched_at") if venue else None
    if not isinstance(fetched, str):
        return False
    try:
        moment = datetime.fromisoformat(fetched)
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return now() - moment > STALE_AFTER


async def _votes_by_idea(
    session: AsyncSession, idea_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[uuid.UUID, str]]:
    """idea id -> {voter id: value}, read as plain columns so it is never stale."""
    votes: dict[uuid.UUID, dict[uuid.UUID, str]] = {}
    if not idea_ids:
        return votes
    rows = await session.execute(
        select(Vote.idea_id, Vote.user_id, Vote.value).where(Vote.idea_id.in_(idea_ids))
    )
    for idea_id, user_id, value in rows.all():
        votes.setdefault(idea_id, {})[user_id] = value
    return votes


def _votes_out(
    votes: dict[uuid.UUID, str], viewer: uuid.UUID, partner: uuid.UUID | None
) -> VotesOut:
    """The partner's vote is shown only once the viewer has voted too (FR-VOTE-2)."""
    mine = votes.get(viewer)
    theirs = votes.get(partner) if partner is not None else None
    return VotesOut(
        mine=cast("Any", mine),
        partner=cast("Any", theirs if mine is not None else None),
    )


def _idea_out(idea: Idea, viewer: uuid.UUID, couple: Couple, votes: VotesOut) -> IdeaOut:
    venue = idea.venue
    attributions = venue.get("attributions", []) if venue else []
    return IdeaOut(
        id=idea.id,
        title=idea.title,
        pitch=idea.pitch,
        tags=[str(t) for t in idea.tags],
        category=idea.category,
        est_cost=None if idea.est_cost is None else float(idea.est_cost),
        currency=idea.currency,
        duration_minutes=idea.duration_minutes,
        best_time=cast("Any", idea.best_time),
        indoor_outdoor=cast("Any", idea.indoor_outdoor),
        grounded=idea.grounded,
        venue=_venue_out(venue, couple),
        event=_event_out(idea.generation),
        valid_until=idea.valid_until,
        status=cast("Any", idea.status),
        may_have_changed=_may_have_changed(venue),
        visibility=cast("Any", idea.visibility),
        source=cast("Any", idea.source),
        created_by_me=idea.created_by_user_id == viewer,
        votes=votes,
        attributions=[str(a) for a in attributions] if isinstance(attributions, list) else [],
    )


async def ideas_out(
    session: AsyncSession, ideas: Sequence[Idea], viewer_user_id: uuid.UUID, couple: Couple
) -> list[IdeaOut]:
    """Several ideas as the viewer sees them, with one query for the votes."""
    partner = await partner_user_id(session, couple.id, viewer_user_id)
    votes = await _votes_by_idea(session, [i.id for i in ideas])
    return [
        _idea_out(
            i, viewer_user_id, couple, _votes_out(votes.get(i.id, {}), viewer_user_id, partner)
        )
        for i in ideas
    ]


async def idea_out(
    session: AsyncSession, idea: Idea, viewer_user_id: uuid.UUID, couple: Couple
) -> IdeaOut:
    return (await ideas_out(session, [idea], viewer_user_id, couple))[0]


# --- My ideas -------------------------------------------------------------------------------


async def list_ideas(
    session: AsyncSession,
    couple_id: uuid.UUID,
    me: uuid.UUID,
    visibility: str,
    status: str,
) -> list[Idea]:
    """`private` is my own ideas only; `shared` is the couple's."""
    query = select(Idea).where(
        Idea.couple_id == couple_id, Idea.visibility == visibility, Idea.status == status
    )
    if visibility == "private":
        query = query.where(Idea.created_by_user_id == me)
    else:
        # A surprise idea stays with its creator (FR-SURP-2), as in ideas_access.
        query = query.where(or_(Idea.is_surprise == false(), Idea.created_by_user_id == me))
    rows = await session.execute(query.order_by(Idea.created_at.desc(), Idea.id))
    return list(rows.scalars().all())


def _money(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value)).quantize(CENT)


async def create_idea(
    session: AsyncSession, couple_id: uuid.UUID, me: uuid.UUID, body: IdeaIn
) -> Idea:
    idea = Idea(
        couple_id=couple_id,
        created_by_user_id=me,
        source="manual",
        visibility="private",
        title=body.title,
        pitch=body.pitch,
        tags=list(body.tags),
        category=body.category,
        est_cost=_money(body.est_cost),
        duration_minutes=body.duration_minutes,
        best_time=body.best_time,
        indoor_outdoor=body.indoor_outdoor,
        grounded=False,
        updated_at=now(),
    )
    session.add(idea)
    await session.flush()
    await session.refresh(idea)  # created_at and the column defaults are filled in by the database
    return idea


async def patch_idea(
    session: AsyncSession,
    couple_id: uuid.UUID,
    me: uuid.UUID,
    idea_id: uuid.UUID,
    body: IdeaPatch,
) -> Idea:
    idea = await get_editable_idea(session, couple_id, me, idea_id)
    sent = body.model_fields_set
    if "title" in sent:
        idea.title = body.title
    if "pitch" in sent:
        idea.pitch = body.pitch
    if "tags" in sent:
        idea.tags = list(body.tags)
    for field in _OPTIONAL_FIELDS:
        if field in sent:
            value = getattr(body, field)
            setattr(idea, field, _money(value) if field == "est_cost" else value)
    idea.updated_at = now()
    await session.flush()
    return idea


async def archive_idea(
    session: AsyncSession, couple_id: uuid.UUID, me: uuid.UUID, idea_id: uuid.UUID
) -> None:
    idea = await get_editable_idea(session, couple_id, me, idea_id)
    if idea.status != "archived":
        idea.status = "archived"
        idea.updated_at = now()
        await session.flush()


async def share_idea(
    session: AsyncSession,
    couple_id: uuid.UUID,
    me: uuid.UUID,
    idea_id: uuid.UUID,
    settings: Settings,
) -> Idea:
    """Private to shared, once and for good (FR-IDEA-0). Only the creator can see a private
    idea, so only they can share it; sharing a shared one changes nothing."""
    idea = await get_visible_idea(session, couple_id, me, idea_id)
    if idea.visibility == "private":
        stamp = now()
        idea.visibility = "shared"
        idea.shared_at = stamp
        idea.updated_at = stamp
        await session.flush()
        if not idea.is_surprise:  # a surprise idea stays with its creator
            emit(session, idea.couple_id, "idea.created", {"id": str(idea.id)}, except_user=me)
            await notify_ideas_added(session, idea.couple_id, me, 1, settings)
    return idea


# --- Deck, votes, lists ---------------------------------------------------------------------


async def deck(session: AsyncSession, couple_id: uuid.UUID, me: uuid.UUID) -> DeckOut:
    """Up to 20 shared active ideas I haven't voted on, oldest first. Unpaired: none."""
    couple = await get_couple(session, couple_id)
    if await partner_user_id(session, couple_id, me) is None:
        return DeckOut(cards=[], partner_joined=False)
    mine = select(Vote.id).where(Vote.idea_id == Idea.id, Vote.user_id == me)
    rows = await session.execute(
        shared_deck_ideas(couple_id, me)
        .where(~mine.exists())
        .order_by(Idea.created_at, Idea.id)
        .limit(DECK_SIZE)
    )
    cards = await ideas_out(session, list(rows.scalars().all()), me, couple)
    return DeckOut(cards=cards, partner_joined=True)


async def _is_locked(
    session: AsyncSession, couple_id: uuid.UUID, viewer_user_id: uuid.UUID, idea_id: uuid.UUID
) -> bool:
    """A date made from this idea is scheduled and the viewer can see it, so their votes are
    frozen (FR-VOTE-5). A hidden surprise date never locks the surprisee out."""
    dates = visible_itineraries(session, viewer_user_id, couple_id).where(
        Itinerary.idea_id == idea_id, Itinerary.status == "scheduled"
    )
    return bool((await session.execute(select(dates.exists()))).scalar_one())


async def cast_vote(
    session: AsyncSession,
    couple_id: uuid.UUID,
    me: uuid.UUID,
    idea_id: uuid.UUID,
    value: str,
    settings: Settings,
) -> Idea:
    """Record or change my vote on a shared idea. When that turns the idea into a match,
    `on_match` is called (not again when a match is re-voted yes)."""
    idea = await get_visible_idea(session, couple_id, me, idea_id)
    if idea.visibility != "shared":
        raise not_found()
    if idea.status != "active":
        raise ApiError(409, "idea_not_active", "This idea isn't on the deck anymore.")
    if await _is_locked(session, couple_id, me, idea.id):
        raise ApiError(409, "vote_locked", "A date has been scheduled from this idea.")
    partner = await partner_user_id(session, couple_id, me)
    votes = (await _votes_by_idea(session, [idea.id])).get(idea.id, {})
    theirs = votes.get(partner) if partner is not None else None
    was_match = votes.get(me) == "yes" and theirs == "yes"
    existing = (
        await session.execute(select(Vote).where(Vote.idea_id == idea.id, Vote.user_id == me))
    ).scalar_one_or_none()
    if existing is None:
        session.add(Vote(idea_id=idea.id, user_id=me, value=value, updated_at=now()))
    else:
        existing.value = value
        existing.updated_at = now()
    await session.flush()
    await taste.schedule_recompute(session, couple_id, user_ids=[me])
    if not idea.is_surprise:  # the partner can't see a surprise idea, so not even its id
        emit(session, couple_id, "vote.updated", {"idea_id": str(idea.id)}, except_user=me)
    if value == "yes" and theirs == "yes" and not was_match:
        await on_match(session, idea, me, settings)
    return idea


async def undo_vote(session: AsyncSession, couple_id: uuid.UUID, me: uuid.UUID) -> Idea | None:
    """Delete my latest vote if it is under ten minutes old and not locked (FR-VOTE-6)."""
    vote = (
        await session.execute(
            select(Vote)
            .where(
                Vote.user_id == me,
                Vote.idea_id.in_(shared_ideas(couple_id, me).with_only_columns(Idea.id)),
            )
            .order_by(Vote.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if vote is None or now() - vote.updated_at > UNDO_WINDOW:
        return None
    if await _is_locked(session, couple_id, me, vote.idea_id):
        return None
    idea = (await session.execute(select(Idea).where(Idea.id == vote.idea_id))).scalar_one()
    await session.delete(vote)
    await session.flush()
    await taste.schedule_recompute(session, couple_id, user_ids=[me])
    if not idea.is_surprise:  # the partner can't see a surprise idea, so not even its id
        emit(session, couple_id, "vote.updated", {"idea_id": str(idea.id)}, except_user=me)
    return idea


async def lists(session: AsyncSession, couple_id: uuid.UUID, me: uuid.UUID) -> IdeaListsOut:
    """My shared, non-archived ideas by how the votes stand. Nothing shows until I've voted."""
    couple = await get_couple(session, couple_id)
    partner = await partner_user_id(session, couple_id, me)
    ideas = (
        (
            await session.execute(
                shared_listed_ideas(couple_id, me).order_by(Idea.created_at.desc(), Idea.id)
            )
        )
        .scalars()
        .all()
    )
    votes = await _votes_by_idea(session, [i.id for i in ideas])
    out = IdeaListsOut(matches=[], maybes=[], pending_partner=[], passed=[])
    for idea in ideas:
        idea_votes = votes.get(idea.id, {})
        mine = idea_votes.get(me)
        if mine is None:
            continue
        theirs = idea_votes.get(partner) if partner is not None else None
        card = _idea_out(idea, me, couple, _votes_out(idea_votes, me, partner))
        if theirs is None:
            out.pending_partner.append(card)
        elif "no" in (mine, theirs):
            out.passed.append(card)
        elif mine == theirs == "yes":
            out.matches.append(card)
        else:
            out.maybes.append(card)
    return out
