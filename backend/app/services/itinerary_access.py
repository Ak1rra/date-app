"""Who may see and edit an itinerary: the one place the rule lives (m3 design, "Visibility").

The planner always sees their own itineraries. The other member sees one only once it has
been announced and is not a surprise; anything else is a 404 for them, so a hidden date never
leaks by existing. Only the planner edits. Every itinerary query goes through here.
"""

import uuid
from typing import Any

from sqlalchemy import Select, and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import Itinerary
from app.services.couple import not_found


def visible_itineraries(
    session: AsyncSession, viewer_user_id: uuid.UUID, couple_id: uuid.UUID
) -> Select[Any]:
    """The itineraries `viewer_user_id` may see in their couple. `session` is part of the
    signature so M4 can look up more (surprise reveal state) without changing callers."""
    # M4 extends the surprise rules here (reveal, redaction); nothing else should re-derive them.
    return select(Itinerary).where(
        Itinerary.couple_id == couple_id,
        or_(
            Itinerary.planner_user_id == viewer_user_id,
            and_(Itinerary.announced_at.is_not(None), Itinerary.is_surprise == false()),
        ),
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
