"""Surprise mode, planner side (M4): hints, the reveal time, and the one place that releases
hints and reveals a surprise (`process_due`)."""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ApiError
from app.models import CoupleMember, Itinerary, SurpriseHint
from app.realtime import emit, emit_itinerary
from app.schemas.surprise import HintIn, HintOut, HintPatch, RevealAtIn
from app.services.couple import not_found, now
from app.services.notify import notify
from app.worker.jobs import enqueue

MAX_HINTS = 20
SURPRISE_DUE = "surprise.due"


# --- Access


def require_surprise_editable(itinerary: Itinerary) -> None:
    """Hints and the reveal time only change while the date is a draft or scheduled surprise."""
    if itinerary.status not in ("draft", "scheduled"):
        raise ApiError(409, "not_editable", f"A {itinerary.status} date can't be changed.")
    if not itinerary.is_surprise:
        raise ApiError(409, "not_a_surprise", "This date isn't a surprise.")


# --- Locking

_LOCKED_COLUMNS = ["status", "is_surprise", "starts_at", "ends_at", "reveal_at", "revealed_at"]


async def _lock(session: AsyncSession, itinerary: Itinerary) -> None:
    """Serialize on the itinerary row and re-read the columns that decide hints and the
    reveal, so a concurrent request or worker job never acts on stale values. Held until the
    caller commits."""
    await session.flush()
    await session.refresh(itinerary, attribute_names=_LOCKED_COLUMNS, with_for_update=True)


# --- Hints


async def _get_hint(
    session: AsyncSession, itinerary: Itinerary, hint_id: uuid.UUID
) -> SurpriseHint:
    hint: SurpriseHint | None = (
        await session.execute(
            select(SurpriseHint)
            .where(SurpriseHint.id == hint_id, SurpriseHint.itinerary_id == itinerary.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if hint is None:
        raise not_found("That hint isn't here.")
    return hint


async def list_hints(session: AsyncSession, itinerary: Itinerary) -> list[HintOut]:
    rows = (
        (
            await session.execute(
                select(SurpriseHint)
                .where(SurpriseHint.itinerary_id == itinerary.id)
                .order_by(SurpriseHint.release_at, SurpriseHint.created_at, SurpriseHint.id)
            )
        )
        .scalars()
        .all()
    )
    return [HintOut.model_validate(row) for row in rows]


async def create_hint(session: AsyncSession, itinerary: Itinerary, body: HintIn) -> SurpriseHint:
    await _lock(session, itinerary)
    require_surprise_editable(itinerary)
    count = (
        await session.execute(
            select(func.count())
            .select_from(SurpriseHint)
            .where(SurpriseHint.itinerary_id == itinerary.id)
        )
    ).scalar_one()
    if count >= MAX_HINTS:
        raise ApiError(409, "too_many_hints", f"A surprise can have at most {MAX_HINTS} hints.")
    hint = SurpriseHint(
        itinerary_id=itinerary.id,
        kind=body.kind,
        text=body.text,
        release_at=body.release_at or now(),
    )
    session.add(hint)
    await session.flush()
    emit_itinerary(session, itinerary, "itinerary.updated", notify_partner=False)
    return hint


async def patch_hint(
    session: AsyncSession, itinerary: Itinerary, hint_id: uuid.UUID, body: HintPatch
) -> SurpriseHint:
    await _lock(session, itinerary)
    require_surprise_editable(itinerary)
    hint = await _get_hint(session, itinerary, hint_id)
    if hint.released_at is not None:
        raise ApiError(409, "already_released", "That hint has already been sent.")
    fields = body.model_fields_set
    if "kind" in fields and body.kind is not None:
        hint.kind = body.kind
    if "text" in fields and body.text is not None:
        hint.text = body.text
    if "release_at" in fields:
        hint.release_at = body.release_at or now()
    await session.flush()
    emit_itinerary(session, itinerary, "itinerary.updated", notify_partner=False)
    return hint


async def delete_hint(session: AsyncSession, itinerary: Itinerary, hint_id: uuid.UUID) -> None:
    await _lock(session, itinerary)
    require_surprise_editable(itinerary)
    await session.delete(await _get_hint(session, itinerary, hint_id))
    await session.flush()
    emit_itinerary(session, itinerary, "itinerary.updated", notify_partner=False)


# --- Reveal time


async def set_reveal_at(session: AsyncSession, itinerary: Itinerary, body: RevealAtIn) -> None:
    """Move the reveal; null puts it back at the start of the date."""
    await _lock(session, itinerary)
    require_surprise_editable(itinerary)
    if itinerary.revealed_at is not None:
        raise ApiError(409, "already_revealed", "This surprise has already been revealed.")
    reveal_at = body.reveal_at or itinerary.starts_at
    latest = itinerary.ends_at or itinerary.starts_at
    if reveal_at is not None and latest is not None and reveal_at > latest:
        raise ApiError(422, "invalid_reveal_at", "The reveal can't come after the date is over.")
    itinerary.reveal_at = reveal_at
    itinerary.updated_at = now()
    await session.flush()
    emit_itinerary(session, itinerary, "itinerary.updated")


async def reveal_now(session: AsyncSession, itinerary: Itinerary, settings: Settings) -> None:
    await _lock(session, itinerary)
    require_surprise_editable(itinerary)
    if itinerary.status != "scheduled":
        raise ApiError(409, "invalid_status", "Schedule the date before revealing it.")
    if itinerary.revealed_at is not None:
        raise ApiError(409, "already_revealed", "This surprise has already been revealed.")
    moment = now()
    itinerary.reveal_at = moment
    itinerary.updated_at = moment
    await session.flush()
    await process_due(session, itinerary, settings, moment)


# --- Release and reveal


async def _surprisee(session: AsyncSession, itinerary: Itinerary) -> uuid.UUID | None:
    """The couple member who isn't the planner, if there is one yet."""
    return (
        await session.execute(
            select(CoupleMember.user_id).where(
                CoupleMember.couple_id == itinerary.couple_id,
                CoupleMember.user_id != itinerary.planner_user_id,
                CoupleMember.left_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def process_due(
    session: AsyncSession, itinerary: Itinerary, settings: Settings, now: dt.datetime
) -> None:
    """Release every due hint and reveal the surprise if its time has come. The only place
    that does either. Does nothing unless this is a scheduled surprise. The caller commits.
    Notifications carry only the planner's own hint text, never a title or a stop (FR-NOTIF-5)."""
    await _lock(session, itinerary)
    if not itinerary.is_surprise or itinerary.status != "scheduled":
        return
    due = (
        (
            await session.execute(
                select(SurpriseHint)
                .where(
                    SurpriseHint.itinerary_id == itinerary.id,
                    SurpriseHint.released_at.is_(None),
                    SurpriseHint.release_at <= now,
                )
                .order_by(SurpriseHint.release_at, SurpriseHint.created_at, SurpriseHint.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    reveal = (
        itinerary.revealed_at is None
        and itinerary.reveal_at is not None
        and itinerary.reveal_at <= now
    )
    if not due and not reveal:
        return
    surprisee = await _surprisee(session, itinerary)
    for hint in due:
        hint.released_at = now
        if surprisee is not None:
            emit(session, itinerary.couple_id, "hint.released", {}, only_user=surprisee)
            await notify(
                session,
                surprisee,
                "hint_released",
                "A new hint for your surprise ✨",
                hint.text,
                {"url": f"/surprise/{itinerary.id}"},
                couple_id=itinerary.couple_id,
                dedupe_key=f"hint:{hint.id}",
                settings=settings,
            )
    if reveal:
        itinerary.revealed_at = now
        itinerary.updated_at = now
        if surprisee is not None:
            emit(
                session,
                itinerary.couple_id,
                "surprise.revealed",
                {"id": str(itinerary.id)},
                only_user=surprisee,
            )
            await notify(
                session,
                surprisee,
                "surprise_revealed",
                "Your surprise is revealed ✨",
                "Tap to see what's planned.",
                {"url": f"/itineraries/{itinerary.id}"},
                couple_id=itinerary.couple_id,
                dedupe_key=f"reveal:{itinerary.id}",
                settings=settings,
            )
    await session.flush()


async def schedule_due_jobs(session: AsyncSession, itinerary: Itinerary, now: dt.datetime) -> None:
    """Enqueue a `surprise.due` job for each distinct release or reveal time after `now`, the
    same instant `process_due` used, so nothing falls between the two. The caller commits."""
    if not itinerary.is_surprise or itinerary.status != "scheduled":
        return
    times: set[dt.datetime] = set(
        (
            await session.execute(
                select(SurpriseHint.release_at).where(
                    SurpriseHint.itinerary_id == itinerary.id,
                    SurpriseHint.released_at.is_(None),
                    SurpriseHint.release_at > now,
                )
            )
        )
        .scalars()
        .all()
    )
    if itinerary.revealed_at is None and itinerary.reveal_at and itinerary.reveal_at > now:
        times.add(itinerary.reveal_at)
    payload: dict[str, Any] = {"itinerary_id": str(itinerary.id)}
    for run_at in sorted(times):
        await enqueue(
            session,
            SURPRISE_DUE,
            payload,
            run_at=run_at,
            dedupe_key=f"surprise:{itinerary.id}:{run_at.isoformat()}",
        )
