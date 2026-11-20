from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import CurrentUser, current_user
from app.schemas.auth import MeOut, MePatch
from app.services.auth import me_out

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=MeOut)
async def get_me(current: CurrentUser = Depends(current_user)) -> MeOut:
    return me_out(current.user, current.couple_id, current.role)


@router.patch("", response_model=MeOut)
async def patch_me(
    body: MePatch,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> MeOut:
    """Update the caller's own profile; there is no id in the path, so only ever yourself."""
    if body.display_name is not None:
        current.user.display_name = body.display_name
    if body.timezone is not None:
        current.user.timezone = body.timezone
    await session.commit()
    return me_out(current.user, current.couple_id, current.role)
