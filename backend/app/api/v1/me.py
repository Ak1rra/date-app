import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import _clear_session_cookies
from app.config import Settings
from app.db import get_session
from app.deps import CurrentUser, current_user, get_app_settings
from app.errors import ApiError
from app.models import CoupleMember, PartnerProfile
from app.schemas.account import AccountDeleteOut, AccountExportOut
from app.schemas.auth import MeOut, MePatch
from app.services import account
from app.services.auth import me_out, revoke_all_sessions
from app.worker.handlers import PURGE_DELAY, USER_PURGE, purge_key
from app.worker.jobs import enqueue

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


@router.get("/export", response_model=AccountExportOut)
async def export_me(
    response: Response,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> AccountExportOut:
    """Download the caller's own data as JSON. Only ever yourself, never the partner's."""
    exported = await account.build_export(session, current.user)
    day = exported.exported_at.date().isoformat()
    response.headers["Content-Disposition"] = f'attachment; filename="dateapp-export-{day}.json"'
    return exported


@router.delete("", response_model=AccountDeleteOut)
async def delete_me(
    response: Response,
    user_id: uuid.UUID = Query(description="The profile the caller means to delete."),
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> AccountDeleteOut:
    """Delete the caller's own account. In NO_LOGIN mode the household user must survive (the
    profile picker needs them), so their data is reset instead. Only ever yourself.

    `user_id` is a guard: the profile the caller saw when they confirmed. If the session has since
    moved to another profile (a switch in this or another tab), nothing is deleted."""
    user = current.user
    if user_id != user.id:
        raise ApiError(
            409,
            "profile_changed",
            "You switched profiles. Check which profile you're on and try again.",
        )
    if settings.no_login:
        await account.delete_personal_data(session, user.id, current.couple_id, keep_name=True)
        await session.commit()
        return AccountDeleteOut(mode="reset")

    user.deleted_at = func.now()
    user.email = f"deleted-{user.id}@deleted.invalid"
    user.display_name = None
    await session.execute(
        update(PartnerProfile)
        .where(PartnerProfile.subject_user_id == user.id)
        .values(display_name=None)
    )
    await session.execute(
        update(CoupleMember)
        .where(CoupleMember.user_id == user.id, CoupleMember.left_at.is_(None))
        .values(left_at=func.now())
    )
    await enqueue(
        session,
        USER_PURGE,
        {"user_id": str(user.id)},
        run_at=dt.datetime.now(dt.UTC) + PURGE_DELAY,
        dedupe_key=purge_key(user.id),
    )
    await revoke_all_sessions(session, user.id)  # commits everything above in one transaction
    _clear_session_cookies(response, settings)
    return AccountDeleteOut(mode="deleted")
