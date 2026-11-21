"""Who may see which idea. Every deck, list and idea lookup goes through here, so a rule about
visibility (a private idea, and later a surprise) is stated once.

A private idea belongs to its creator; anyone else in the couple gets a 404, the same as for an
idea that doesn't exist.
"""

import uuid
from typing import Any

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Idea
from app.services.couple import not_found


def _shared_ideas(couple_id: uuid.UUID, viewer_user_id: uuid.UUID) -> Select[Idea]:
    # M4 adds the exclusion of a surprise the viewer is the unrevealed surprisee of, here.
    return select(Idea).where(Idea.couple_id == couple_id, Idea.visibility == "shared")


def shared_deck_ideas(couple_id: uuid.UUID, viewer_user_id: uuid.UUID) -> Select[Idea]:
    """The couple's shared ideas that are still active."""
    return _shared_ideas(couple_id, viewer_user_id).where(Idea.status == "active")


def shared_listed_ideas(couple_id: uuid.UUID, viewer_user_id: uuid.UUID) -> Select[Idea]:
    """The couple's shared ideas that aren't archived (active ones and expired ones)."""
    return _shared_ideas(couple_id, viewer_user_id).where(Idea.status != "archived")


def _visible_to(couple_id: uuid.UUID, viewer_user_id: uuid.UUID) -> Any:
    return and_(
        Idea.couple_id == couple_id,
        or_(
            Idea.visibility == "shared",
            and_(Idea.visibility == "private", Idea.created_by_user_id == viewer_user_id),
        ),
    )


async def get_visible_idea(
    session: AsyncSession, couple_id: uuid.UUID, viewer_user_id: uuid.UUID, idea_id: uuid.UUID
) -> Idea:
    """A shared idea of the couple (any status), or a private one the viewer created."""
    idea = (
        await session.execute(
            select(Idea).where(Idea.id == idea_id, _visible_to(couple_id, viewer_user_id))
        )
    ).scalar_one_or_none()
    if idea is None:
        raise not_found()
    return idea


async def get_editable_idea(
    session: AsyncSession, couple_id: uuid.UUID, viewer_user_id: uuid.UUID, idea_id: uuid.UUID
) -> Idea:
    """The same rule as seeing: a shared idea is editable by either member (FR-IDEA-3), a
    private one by its creator only."""
    return await get_visible_idea(session, couple_id, viewer_user_id, idea_id)
