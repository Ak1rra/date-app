"""Partner profiles and availability.

`can_edit` is the one rule that decides every write: a profile is editable while nobody has
claimed it (the planner fills in the partner before she joins, FR-PROF-7) and by its subject
afterwards. Anyone else gets a 403, because they can see the profile but not change it.
Functions flush but leave the commit to the caller.
"""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import AvailabilityBlock, AvailabilityWindow, PartnerProfile
from app.schemas.profiles import AvailabilityIO, BlockIO, ProfileIn, ProfileOut, WindowIO
from app.services.couple import not_found, now


def can_edit(profile: PartnerProfile, me: uuid.UUID) -> bool:
    return profile.subject_user_id is None or profile.subject_user_id == me


def profile_out(profile: PartnerProfile, me: uuid.UUID) -> ProfileOut:
    return ProfileOut.model_validate(
        {
            "id": profile.id,
            "slot": profile.slot,
            "is_me": profile.subject_user_id == me,
            "subject_joined": profile.subject_user_id is not None,
            "can_edit": can_edit(profile, me),
            "display_name": profile.display_name,
            "liked_categories": profile.liked_categories,
            "disliked_categories": profile.disliked_categories,
            "cuisines_liked": profile.cuisines_liked,
            "cuisines_disliked": profile.cuisines_disliked,
            "energy": profile.energy,
            "indoor_outdoor": profile.indoor_outdoor,
            "social_setting": profile.social_setting,
            "loves": profile.loves,
            "hard_nos": profile.hard_nos,
            "dietary": profile.dietary,
            "allergies": profile.allergies,
            "accessibility": profile.accessibility,
            "edited_by_user_id": profile.edited_by_user_id,
            "confirmed_by_subject_at": profile.confirmed_by_subject_at,
            "updated_at": profile.updated_at,
        }
    )


async def get_profile(
    session: AsyncSession,
    couple_id: uuid.UUID,
    profile_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> PartnerProfile:
    query = select(PartnerProfile).where(
        PartnerProfile.id == profile_id, PartnerProfile.couple_id == couple_id
    )
    if for_update:
        query = query.with_for_update()
    profile = (await session.execute(query)).scalar_one_or_none()
    if profile is None:
        raise not_found()
    return profile


async def _get_editable_profile(
    session: AsyncSession, couple_id: uuid.UUID, profile_id: uuid.UUID, me: uuid.UUID
) -> PartnerProfile:
    # Lock the row so a partner joining (which sets subject_user_id on it) cannot slip in between
    # this check and the write: the join waits for our commit, or we see the claim and refuse.
    profile = await get_profile(session, couple_id, profile_id, for_update=True)
    if not can_edit(profile, me):
        raise ApiError(403, "forbidden", "Only the person this profile is about can change it.")
    return profile


async def list_profiles(
    session: AsyncSession, couple_id: uuid.UUID, me: uuid.UUID
) -> list[ProfileOut]:
    rows = await session.execute(
        select(PartnerProfile)
        .where(PartnerProfile.couple_id == couple_id)
        .order_by(PartnerProfile.slot)  # 'owner' sorts before 'partner'
    )
    return [profile_out(p, me) for p in rows.scalars().all()]


async def put_profile(
    session: AsyncSession,
    couple_id: uuid.UUID,
    profile_id: uuid.UUID,
    me: uuid.UUID,
    body: ProfileIn,
) -> ProfileOut:
    profile = await _get_editable_profile(session, couple_id, profile_id, me)
    for field, value in body.model_dump().items():
        setattr(profile, field, value)
    profile.edited_by_user_id = me
    profile.updated_at = now()
    await session.flush()
    return profile_out(profile, me)


async def _availability(session: AsyncSession, profile_id: uuid.UUID) -> AvailabilityIO:
    windows = (
        await session.execute(
            select(AvailabilityWindow)
            .where(AvailabilityWindow.profile_id == profile_id)
            .order_by(AvailabilityWindow.weekday, AvailabilityWindow.start_time)
        )
    ).scalars()
    blocks = (
        await session.execute(
            select(AvailabilityBlock)
            .where(AvailabilityBlock.profile_id == profile_id)
            .order_by(AvailabilityBlock.date, AvailabilityBlock.created_at)
        )
    ).scalars()
    return AvailabilityIO(
        windows=[WindowIO.model_validate(w) for w in windows],
        blocks=[BlockIO.model_validate(b) for b in blocks],
    )


async def get_availability(
    session: AsyncSession, couple_id: uuid.UUID, profile_id: uuid.UUID
) -> AvailabilityIO:
    profile = await get_profile(session, couple_id, profile_id)
    return await _availability(session, profile.id)


async def put_availability(
    session: AsyncSession,
    couple_id: uuid.UUID,
    profile_id: uuid.UUID,
    me: uuid.UUID,
    body: AvailabilityIO,
) -> AvailabilityIO:
    """Replace the profile's windows and blocks; the caller commits, so it is one transaction."""
    profile = await _get_editable_profile(session, couple_id, profile_id, me)
    await session.execute(
        delete(AvailabilityWindow).where(AvailabilityWindow.profile_id == profile.id)
    )
    await session.execute(
        delete(AvailabilityBlock).where(AvailabilityBlock.profile_id == profile.id)
    )
    session.add_all(
        [
            AvailabilityWindow(
                profile_id=profile.id,
                weekday=w.weekday,
                start_time=w.start_time,
                end_time=w.end_time,
            )
            for w in body.windows
        ]
    )
    session.add_all(
        [AvailabilityBlock(profile_id=profile.id, date=b.date, note=b.note) for b in body.blocks]
    )
    await session.flush()
    return await _availability(session, profile.id)
