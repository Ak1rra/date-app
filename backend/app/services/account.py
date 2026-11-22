"""Account export and deletion (FR-AUTH-3, NFR-PRIV-2).

Everything here is keyed on one user id: the export reads only that user's rows, and
`delete_personal_data` removes only that user's rows. Nothing commits; callers do.
"""

import datetime as dt
import uuid
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AvailabilityBlock,
    AvailabilityWindow,
    ChatMessage,
    ChatThread,
    Idea,
    ImportantDate,
    Notification,
    PartnerProfile,
    PlannerNote,
    PlannerRead,
    PlanSession,
    PlanTemplate,
    ProfileSuggestion,
    PushSubscription,
    Rating,
    TasteSummary,
    User,
    Vote,
)
from app.schemas.account import AccountExportOut, ExportProfile, ExportUser


def _row(obj: Any) -> dict[str, Any]:
    """All of a row's columns as JSON-ready values."""
    return {
        attr.key: jsonable_encoder(getattr(obj, attr.key))
        for attr in inspect(type(obj)).column_attrs
    }


async def _rows(session: AsyncSession, model: Any, *where: Any) -> list[dict[str, Any]]:
    result = await session.execute(select(model).where(*where).order_by(model.created_at))
    return [_row(obj) for obj in result.scalars().all()]


async def build_export(session: AsyncSession, user: User) -> AccountExportOut:
    """The caller's own data. Every query filters on `user.id`, so a partner's private rows
    (votes, ratings, notes, reads, surprises planned for the caller) are never selected."""
    profile_row = (
        await session.execute(
            select(PartnerProfile)
            .where(PartnerProfile.subject_user_id == user.id)
            .order_by(PartnerProfile.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    profile: ExportProfile | None = None
    if profile_row is not None:
        profile = ExportProfile(
            profile=_row(profile_row),
            availability_windows=await _rows(
                session, AvailabilityWindow, AvailabilityWindow.profile_id == profile_row.id
            ),
            availability_blocks=await _rows(
                session, AvailabilityBlock, AvailabilityBlock.profile_id == profile_row.id
            ),
            important_dates=await _rows(
                session, ImportantDate, ImportantDate.profile_id == profile_row.id
            ),
        )

    messages = (
        await session.execute(
            select(ChatMessage, ChatThread.scope)
            .join(ChatThread, ChatThread.id == ChatMessage.thread_id)
            .where(ChatMessage.author_user_id == user.id)
            .order_by(ChatMessage.created_at)
        )
    ).all()
    chat_messages = [
        {
            "id": jsonable_encoder(m.id),
            "thread_id": jsonable_encoder(m.thread_id),
            "scope": scope,
            "role": m.role,
            "content": m.content,
            "created_at": jsonable_encoder(m.created_at),
        }
        for m, scope in messages
    ]

    return AccountExportOut(
        exported_at=dt.datetime.now(dt.UTC),
        user=ExportUser(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            timezone=user.timezone,
            notification_settings=user.notification_settings,
            created_at=user.created_at,
        ),
        profile=profile,
        planner_notes=await _rows(session, PlannerNote, PlannerNote.author_user_id == user.id),
        ideas=await _rows(session, Idea, Idea.created_by_user_id == user.id),
        votes=await _rows(session, Vote, Vote.user_id == user.id),
        ratings=await _rows(session, Rating, Rating.user_id == user.id),
        planner_reads=await _rows(session, PlannerRead, PlannerRead.planner_user_id == user.id),
        chat_messages=chat_messages,
        notifications=await _rows(session, Notification, Notification.user_id == user.id),
    )


async def delete_personal_data(
    session: AsyncSession,
    user_id: uuid.UUID,
    couple_id: uuid.UUID | None,
    *,
    keep_name: bool,
) -> None:
    """Delete one user's personal rows and reset their profile preferences. Idempotent.

    Shared ideas and itineraries are couple data and stay. With no `couple_id` only the
    user-scoped rows go. The caller commits."""
    await session.execute(delete(Vote).where(Vote.user_id == user_id))
    await session.execute(delete(Rating).where(Rating.user_id == user_id))
    await session.execute(delete(PlannerRead).where(PlannerRead.planner_user_id == user_id))
    await session.execute(delete(PlannerNote).where(PlannerNote.author_user_id == user_id))
    await session.execute(delete(Notification).where(Notification.user_id == user_id))
    await session.execute(delete(PushSubscription).where(PushSubscription.user_id == user_id))
    await session.execute(delete(PlanSession).where(PlanSession.planner_user_id == user_id))
    await session.execute(delete(PlanTemplate).where(PlanTemplate.planner_user_id == user_id))
    await session.execute(
        delete(ProfileSuggestion).where(ProfileSuggestion.author_user_id == user_id)
    )
    if couple_id is None:
        return

    await session.execute(
        delete(Idea).where(
            Idea.couple_id == couple_id,
            Idea.created_by_user_id == user_id,
            Idea.visibility == "private",
        )
    )
    # The private thread's messages go with it (ON DELETE CASCADE).
    await session.execute(
        delete(ChatThread).where(
            ChatThread.couple_id == couple_id,
            ChatThread.scope == "private",
            ChatThread.owner_user_id == user_id,
        )
    )
    shared_threads = select(ChatThread.id).where(
        ChatThread.couple_id == couple_id, ChatThread.scope == "shared"
    )
    await session.execute(
        delete(ChatMessage).where(
            ChatMessage.author_user_id == user_id, ChatMessage.thread_id.in_(shared_threads)
        )
    )

    profile_ids = select(PartnerProfile.id).where(
        PartnerProfile.couple_id == couple_id, PartnerProfile.subject_user_id == user_id
    )
    await session.execute(
        delete(TasteSummary).where(TasteSummary.about_profile_id.in_(profile_ids))
    )
    await session.execute(
        delete(AvailabilityWindow).where(AvailabilityWindow.profile_id.in_(profile_ids))
    )
    await session.execute(
        delete(AvailabilityBlock).where(AvailabilityBlock.profile_id.in_(profile_ids))
    )
    await session.execute(
        delete(ImportantDate).where(
            ImportantDate.couple_id == couple_id,
            ImportantDate.created_by_user_id == user_id,
            ImportantDate.profile_id.is_not(None),
        )
    )
    await session.execute(
        update(PartnerProfile)
        .where(PartnerProfile.couple_id == couple_id, PartnerProfile.subject_user_id == user_id)
        .values(
            liked_categories=[],
            disliked_categories=[],
            cuisines_liked=[],
            cuisines_disliked=[],
            dietary=[],
            allergies=[],
            energy=None,
            indoor_outdoor=None,
            social_setting=None,
            loves=None,
            hard_nos=None,
            accessibility=None,
            confirmed_by_subject_at=None,
            **({"display_name": None} if not keep_name else {}),
        )
    )
