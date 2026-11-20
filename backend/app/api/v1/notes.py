import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import CurrentUser, current_user
from app.schemas.notes import NoteIn, NoteKind, NoteOut, NotePatch
from app.services import notes as note_service
from app.services.couple import require_couple_id

router = APIRouter(prefix="/notes", tags=["notes"])


@router.get("", response_model=list[NoteOut])
async def list_notes(
    kind: NoteKind | None = None,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> list[NoteOut]:
    """The caller's own live notes, newest first. Nobody else's are ever visible."""
    rows = await note_service.list_notes(
        session, require_couple_id(current.couple_id), current.user.id, kind
    )
    return [NoteOut.model_validate(r) for r in rows]


@router.post("", response_model=NoteOut, status_code=201)
async def create_note(
    body: NoteIn,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> NoteOut:
    note = await note_service.create_note(
        session, require_couple_id(current.couple_id), current.user.id, current.role, body
    )
    await session.commit()
    return NoteOut.model_validate(note)


@router.patch("/{note_id}", response_model=NoteOut)
async def patch_note(
    note_id: uuid.UUID,
    body: NotePatch,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> NoteOut:
    note = await note_service.patch_note(
        session, require_couple_id(current.couple_id), current.user.id, note_id, body
    )
    await session.commit()
    return NoteOut.model_validate(note)


@router.delete("/{note_id}", status_code=204)
async def delete_note(
    note_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> None:
    await note_service.delete_note(
        session, require_couple_id(current.couple_id), current.user.id, note_id
    )
    await session.commit()
