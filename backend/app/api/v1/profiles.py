import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import CurrentUser, current_user
from app.schemas.profiles import (
    AvailabilityIO,
    ProfileIn,
    ProfileOut,
    ProfileSuggestionIn,
    ProfileSuggestionOut,
)
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


@router.post("/{profile_id}/confirm", response_model=ProfileOut)
async def confirm_profile(
    profile_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ProfileOut:
    """The subject confirms a profile someone else filled in. 403 for anyone else; idempotent."""
    out = await profile_service.confirm_profile(
        session, require_couple_id(current.couple_id), profile_id, current.user.id
    )
    await session.commit()
    return out


@router.post("/{profile_id}/suggestions", response_model=ProfileSuggestionOut, status_code=201)
async def create_suggestion(
    profile_id: uuid.UUID,
    body: ProfileSuggestionIn,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ProfileSuggestionOut:
    """Suggest changes to a joined partner's profile. 409 `edit_directly` if the caller may edit."""
    out = await profile_service.create_suggestion(
        session, require_couple_id(current.couple_id), profile_id, current.user.id, body
    )
    await session.commit()
    return out


@router.get("/{profile_id}/suggestions", response_model=list[ProfileSuggestionOut])
async def list_suggestions(
    profile_id: uuid.UUID,
    status: Literal["pending", "accepted", "declined"] | None = None,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> list[ProfileSuggestionOut]:
    """Newest first. The subject sees all of them; anyone else only the ones they wrote."""
    return await profile_service.list_suggestions(
        session, require_couple_id(current.couple_id), profile_id, current.user.id, status
    )


@router.post("/{profile_id}/suggestions/{suggestion_id}/accept", response_model=ProfileOut)
async def accept_suggestion(
    profile_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ProfileOut:
    """Subject only: apply the suggestion as a PUT of the merged profile."""
    out = await profile_service.accept_suggestion(
        session, require_couple_id(current.couple_id), profile_id, suggestion_id, current.user.id
    )
    await session.commit()
    return out


@router.post(
    "/{profile_id}/suggestions/{suggestion_id}/decline", response_model=ProfileSuggestionOut
)
async def decline_suggestion(
    profile_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ProfileSuggestionOut:
    """Subject only: leave the profile as it is."""
    out = await profile_service.decline_suggestion(
        session, require_couple_id(current.couple_id), profile_id, suggestion_id, current.user.id
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
