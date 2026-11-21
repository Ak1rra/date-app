"""Who may see and edit an itinerary: the one place the rule lives (m3 design, "Visibility").

The planner always sees their own itineraries. The other member sees one only once it has
been announced and is not a surprise, or once a surprise has been revealed; anything else is a
404 for them, so a hidden date never leaks by existing. Before the reveal the surprisee reaches a
surprise only through `upcoming_surprises` (the redacted view and the calendar entry). Only the
planner edits. Every itinerary query goes through here.
"""

import uuid
from typing import Any

from sqlalchemy import Select, and_, false, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import Itinerary
from app.services.couple import not_found


def visible_itineraries(
    session: AsyncSession, viewer_user_id: uuid.UUID, couple_id: uuid.UUID
) -> Select[Any]:
    """The itineraries `viewer_user_id` may see in their couple: their own, announced ones that
    aren't surprises, and surprises that have been revealed. `session` is part of the signature
    so callers needn't change if the rule ever needs a lookup. Nothing else re-derives this."""
    return select(Itinerary).where(
        Itinerary.couple_id == couple_id,
        or_(
            Itinerary.planner_user_id == viewer_user_id,
            and_(Itinerary.announced_at.is_not(None), Itinerary.is_surprise == false()),
            and_(Itinerary.is_surprise == true(), Itinerary.revealed_at.is_not(None)),
        ),
    )


def upcoming_surprises(
    session: AsyncSession, viewer_user_id: uuid.UUID, couple_id: uuid.UUID
) -> Select[Any]:
    """The scheduled surprises `viewer_user_id` is the unrevealed surprisee of: same couple,
    planned by someone else, not yet revealed. The only way to reach one before its reveal."""
    return select(Itinerary).where(
        Itinerary.couple_id == couple_id,
        Itinerary.planner_user_id != viewer_user_id,
        Itinerary.is_surprise == true(),
        Itinerary.revealed_at.is_(None),
        Itinerary.status == "scheduled",
        Itinerary.starts_at.is_not(None),
    )


async def get_visible(
    session: AsyncSession,
    viewer_user_id: uuid.UUID,
    couple_id: uuid.UUID,
    itinerary_id: uuid.UUID,
) -> Itinerary:
    """The itinerary, or 404 if it doesn't exist or isn't visible to the viewer."""
    query = visible_itineraries(session, viewer_user_id, couple_id).where(
        Itinerary.id == itinerary_id
    )
    itinerary: Itinerary | None = (await session.execute(query)).scalar_one_or_none()
    if itinerary is None:
        raise not_found("That date isn't here.")
    return itinerary


async def get_editable(
    session: AsyncSession,
    viewer_user_id: uuid.UUID,
    couple_id: uuid.UUID,
    itinerary_id: uuid.UUID,
) -> Itinerary:
    """The itinerary for its planner: 404 if not visible, 403 if visible to a non-planner."""
    itinerary = await get_visible(session, viewer_user_id, couple_id, itinerary_id)
    if itinerary.planner_user_id != viewer_user_id:
        raise ApiError(403, "forbidden", "Only the person planning this date can change it.")
    return itinerary
