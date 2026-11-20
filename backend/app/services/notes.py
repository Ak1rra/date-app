"""Planner notes: private to their author (FR-PROF-8).

A note that exists but belongs to someone else is a 404, the same as one that doesn't exist.
Functions flush but leave the commit to the caller.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import PartnerProfile, PlannerNote
from app.schemas.notes import NoteIn, NotePatch
from app.services.couple import not_found, now


async def list_notes(
    session: AsyncSession, couple_id: uuid.UUID, me: uuid.UUID, kind: str | None
) -> list[PlannerNote]:
    query = select(PlannerNote).where(
        PlannerNote.couple_id == couple_id,
        PlannerNote.author_user_id == me,
        PlannerNote.archived_at.is_(None),
    )
    if kind is not None:
        query = query.where(PlannerNote.kind == kind)
    rows = await session.execute(query.order_by(PlannerNote.created_at.desc()))
    return list(rows.scalars().all())


async def create_note(
    session: AsyncSession,
    couple_id: uuid.UUID,
    me: uuid.UUID,
    role: str | None,
    body: NoteIn,
) -> PlannerNote:
    """A note is about the other person, never the author's own profile (their own slot)."""
    about = (
        await session.execute(
            select(PartnerProfile).where(
                PartnerProfile.id == body.about_profile_id, PartnerProfile.couple_id == couple_id
            )
        )
    ).scalar_one_or_none()
    if about is None:
        raise ApiError(422, "invalid_profile", "That profile isn't in your couple space.")
    if about.subject_user_id == me or about.slot == role:
        raise ApiError(422, "invalid_profile", "Notes are about your partner, not yourself.")
    note = PlannerNote(
        couple_id=couple_id,
        author_user_id=me,
        about_profile_id=about.id,
        kind=body.kind,
        text=body.text,
        updated_at=now(),
    )
    session.add(note)
    await session.flush()
    await session.refresh(note)  # created_at is filled in by the database
    return note


async def _get_note(
    session: AsyncSession, couple_id: uuid.UUID, me: uuid.UUID, note_id: uuid.UUID
) -> PlannerNote:
    note = (
        await session.execute(
            select(PlannerNote).where(
                PlannerNote.id == note_id,
                PlannerNote.couple_id == couple_id,
                PlannerNote.author_user_id == me,
            )
        )
    ).scalar_one_or_none()
    if note is None:
        raise not_found()
    return note


async def patch_note(
    session: AsyncSession,
    couple_id: uuid.UUID,
    me: uuid.UUID,
    note_id: uuid.UUID,
    body: NotePatch,
) -> PlannerNote:
    note = await _get_note(session, couple_id, me, note_id)
    stamp = now()
    if body.text is not None:
        note.text = body.text
    if body.archived is True and note.archived_at is None:
        note.archived_at = stamp
    elif body.archived is False:
        note.archived_at = None
    note.updated_at = stamp
    await session.flush()
    return note


async def delete_note(
    session: AsyncSession, couple_id: uuid.UUID, me: uuid.UUID, note_id: uuid.UUID
) -> None:
    await session.delete(await _get_note(session, couple_id, me, note_id))
    await session.flush()
