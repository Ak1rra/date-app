import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import CurrentUser, current_user
from app.schemas.profiles import AvailabilityIO, ProfileIn, ProfileOut
from app.services import profiles as profile_service
from app.services.couple import require_couple_id

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get("", response_model=list[ProfileOut])
async def list_profiles(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> list[ProfileOut]:
    return await profile_service.list_profiles(
        session, require_couple_id(current.couple_id), current.user.id
    )


@router.get("/{profile_id}", response_model=ProfileOut)
async def get_profile(
    profile_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ProfileOut:
    profile = await profile_service.get_profile(
        session, require_couple_id(current.couple_id), profile_id
    )
    return profile_service.profile_out(profile, current.user.id)


@router.put("/{profile_id}", response_model=ProfileOut)
async def put_profile(
    profile_id: uuid.UUID,
    body: ProfileIn,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ProfileOut:
    """Full replace. 403 if the profile's subject has joined and is not the caller."""
    out = await profile_service.put_profile(
        session, require_couple_id(current.couple_id), profile_id, current.user.id, body
    )
    await session.commit()
    return out


@router.get("/{profile_id}/availability", response_model=AvailabilityIO)
async def get_availability(
    profile_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> AvailabilityIO:
    return await profile_service.get_availability(
        session, require_couple_id(current.couple_id), profile_id
    )


@router.put("/{profile_id}/availability", response_model=AvailabilityIO)
async def put_availability(
    profile_id: uuid.UUID,
    body: AvailabilityIO,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> AvailabilityIO:
    """Replace-all, under the same edit rule as the profile itself."""
    out = await profile_service.put_availability(
        session, require_couple_id(current.couple_id), profile_id, current.user.id, body
    )
    await session.commit()
    return out
