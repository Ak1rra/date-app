import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import get_session
from app.deps import (
    CurrentUser,
    client_ip,
    current_user,
    get_app_settings,
    get_verify_ip_limiter,
    require_email_login,
)
from app.errors import rate_limited
from app.providers.geocoding.base import Geocoder
from app.providers.registry import get_geocoder
from app.schemas.auth import MeOut
from app.schemas.couple import (
    ConstraintsIn,
    ConstraintsOut,
    CoupleOut,
    CouplePatch,
    HomeArea,
    HomeAreaIn,
    ImportantDateIn,
    ImportantDateOut,
    ImportantDatePatch,
)
from app.schemas.pairing import InviteCurrentOut, InviteOut, JoinIn
from app.security.ratelimit import SlidingWindowLimiter
from app.services import couple as couple_service
from app.services import pairing as pairing_service
from app.services.couple import require_couple_id

router = APIRouter(prefix="/couple", tags=["couple"])


@router.get("", response_model=CoupleOut)
async def get_couple(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> CoupleOut:
    return await couple_service.get_couple(session, require_couple_id(current.couple_id))


@router.patch("", response_model=CoupleOut)
async def patch_couple(
    body: CouplePatch,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> CoupleOut:
    out = await couple_service.patch_couple(session, require_couple_id(current.couple_id), body)
    await session.commit()
    return out


@router.put("/home-area", response_model=HomeArea)
async def put_home_area(
    body: HomeAreaIn,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),  # before the provider: 401 beats 503
    geocoder: Geocoder = Depends(get_geocoder),
) -> HomeArea:
    """Geocodes the query and stores only the label and coarse coordinates, never the query."""
    out = await couple_service.set_home_area(
        session, geocoder, require_couple_id(current.couple_id), body.query
    )
    await session.commit()
    return out


@router.get("/constraints", response_model=ConstraintsOut)
async def get_constraints(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ConstraintsOut:
    row = await couple_service.get_constraints(session, require_couple_id(current.couple_id))
    await session.commit()  # the row is created on first read if it is somehow missing
    return ConstraintsOut.model_validate(row)


@router.put("/constraints", response_model=ConstraintsOut)
async def put_constraints(
    body: ConstraintsIn,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ConstraintsOut:
    row = await couple_service.put_constraints(
        session, require_couple_id(current.couple_id), current.user.id, body
    )
    await session.commit()
    return ConstraintsOut.model_validate(row)


@router.get("/important-dates", response_model=list[ImportantDateOut])
async def list_important_dates(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> list[ImportantDateOut]:
    rows = await couple_service.list_important_dates(session, require_couple_id(current.couple_id))
    return [ImportantDateOut.model_validate(r) for r in rows]


@router.post("/important-dates", response_model=ImportantDateOut, status_code=201)
async def create_important_date(
    body: ImportantDateIn,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ImportantDateOut:
    row = await couple_service.create_important_date(
        session, require_couple_id(current.couple_id), current.user.id, body
    )
    await session.commit()
    return ImportantDateOut.model_validate(row)


@router.patch("/important-dates/{date_id}", response_model=ImportantDateOut)
async def patch_important_date(
    date_id: uuid.UUID,
    body: ImportantDatePatch,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> ImportantDateOut:
    row = await couple_service.patch_important_date(
        session, require_couple_id(current.couple_id), date_id, body
    )
    await session.commit()
    return ImportantDateOut.model_validate(row)


@router.delete("/important-dates/{date_id}", status_code=204)
async def delete_important_date(
    date_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> None:
    await couple_service.delete_important_date(
        session, require_couple_id(current.couple_id), date_id
    )
    await session.commit()


@router.post(
    "/invites",
    response_model=InviteOut,
    status_code=201,
    dependencies=[Depends(require_email_login)],
)
async def create_invite(
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> InviteOut:
    """Owner only: a fresh invitation to the couple, revoking the previous one."""
    out = await pairing_service.create_invite(
        session, settings, current.user, current.couple_id, current.role
    )
    await session.commit()
    return out


@router.get(
    "/invites/current",
    response_model=InviteCurrentOut,
    dependencies=[Depends(require_email_login)],
)
async def get_current_invite(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> InviteCurrentOut:
    return await pairing_service.current_invite(session, current.couple_id, current.role)


@router.delete("/invites/current", status_code=204, dependencies=[Depends(require_email_login)])
async def revoke_current_invite(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> None:
    await pairing_service.revoke_current_invite(session, current.couple_id, current.role)
    await session.commit()


@router.post("/join", response_model=MeOut, dependencies=[Depends(require_email_login)])
async def join_couple(
    body: JoinIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    ip_limiter: SlidingWindowLimiter = Depends(get_verify_ip_limiter),
    current: CurrentUser = Depends(current_user),
) -> MeOut:
    """Redeem an invitation (its token or short code) and become the couple's partner."""
    if (wait := ip_limiter.hit(client_ip(request))) is not None:
        raise rate_limited(wait)
    out = await pairing_service.join(session, current.user, body)
    await session.commit()
    return out


@router.post("/leave", status_code=204, dependencies=[Depends(require_email_login)])
async def leave_couple(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> None:
    await pairing_service.leave(session, current.user.id, current.couple_id)
    await session.commit()
