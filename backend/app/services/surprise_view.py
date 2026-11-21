"""What the surprisee sees of an unrevealed surprise: times and released hints, nothing else.

Selects only the id and the times of the itinerary, never its title or stops. Reads only.
"""

import uuid
from typing import Any, cast

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Itinerary, SurpriseHint
from app.schemas.surprise_view import ReleasedHintOut, SurpriseOut
from app.services.couple import not_found
from app.services.itinerary_access import upcoming_surprises


async def _hints(
    session: AsyncSession, itinerary_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[ReleasedHintOut]]:
    hints: dict[uuid.UUID, list[ReleasedHintOut]] = {id_: [] for id_ in itinerary_ids}
    if not itinerary_ids:
        return hints
    rows = await session.execute(
        select(SurpriseHint)
        .where(SurpriseHint.itinerary_id.in_(itinerary_ids), SurpriseHint.released_at.is_not(None))
        .order_by(SurpriseHint.released_at, SurpriseHint.created_at)
    )
    for hint in rows.scalars():
        assert hint.released_at is not None
        hints[hint.itinerary_id].append(
            ReleasedHintOut(
                id=hint.id, kind=hint.kind, text=hint.text, released_at=hint.released_at
            )
        )
    return hints


async def _views(session: AsyncSession, query: Select[Any]) -> list[SurpriseOut]:
    rows = (await session.execute(query)).all()
    hints = await _hints(session, [row.id for row in rows])
    return [
        SurpriseOut(
            id=row.id,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
            reveal_at=row.reveal_at,
            hints=hints[row.id],
        )
        for row in rows
    ]


def _narrow(session: AsyncSession, viewer_user_id: uuid.UUID, couple_id: uuid.UUID) -> Select[Any]:
    """upcoming_surprises narrowed to the columns the view may carry."""
    return cast(
        "Select[Any]",
        upcoming_surprises(session, viewer_user_id, couple_id).with_only_columns(
            Itinerary.id, Itinerary.starts_at, Itinerary.ends_at, Itinerary.reveal_at
        ),
    )


async def list_surprises(
    session: AsyncSession, viewer_user_id: uuid.UUID, couple_id: uuid.UUID
) -> list[SurpriseOut]:
    query = _narrow(session, viewer_user_id, couple_id).order_by(
        Itinerary.starts_at, Itinerary.created_at
    )
    return await _views(session, query)


async def get_surprise(
    session: AsyncSession,
    viewer_user_id: uuid.UUID,
    couple_id: uuid.UUID,
    itinerary_id: uuid.UUID,
) -> SurpriseOut:
    query = _narrow(session, viewer_user_id, couple_id).where(Itinerary.id == itinerary_id)
    views = await _views(session, query)
    if not views:
        raise not_found("That date isn't here.")
    return views[0]
