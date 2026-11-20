import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import CurrentUser, current_user
from app.providers.geocoding.base import Geocoder
from app.providers.registry import get_geocoder
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
from app.services import couple as couple_service
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
