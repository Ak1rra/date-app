"""The couple space: the couple itself, its home area, constraints and important dates.

Every query is scoped to the caller's couple; an id from another couple is a 404, never a
403, so existence doesn't leak. Functions flush but leave the commit to the caller.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import (
    Couple,
    CoupleConstraints,
    CoupleMember,
    ImportantDate,
    PartnerProfile,
    User,
)
from app.providers.geocoding.base import Geocoder
from app.schemas.couple import (
    ConstraintsIn,
    CoupleOut,
    CouplePatch,
    HomeArea,
    ImportantDateIn,
    ImportantDatePatch,
    MemberOut,
)

CENT = Decimal("0.01")


def not_found(message: str = "Not found.") -> ApiError:
    return ApiError(404, "not_found", message)


def require_couple_id(couple_id: uuid.UUID | None) -> uuid.UUID:
    """The caller's couple; a signed-in user without one has nothing to look at."""
    if couple_id is None:
        raise not_found("You don't have a couple space yet.")
    return couple_id


def now() -> datetime:
    return datetime.now(UTC)


def _money(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value)).quantize(CENT)


async def _get_couple(session: AsyncSession, couple_id: uuid.UUID) -> Couple:
    couple = (
        await session.execute(select(Couple).where(Couple.id == couple_id))
    ).scalar_one_or_none()
    if couple is None:
        raise not_found()
    return couple


async def couple_out(session: AsyncSession, couple: Couple) -> CoupleOut:
    rows = (
        await session.execute(
            select(CoupleMember.user_id, User.display_name, CoupleMember.role)
            .join(User, User.id == CoupleMember.user_id)
            .where(CoupleMember.couple_id == couple.id, CoupleMember.left_at.is_(None))
            .order_by(CoupleMember.role)  # 'owner' sorts before 'partner'
        )
    ).all()
    members = [MemberOut(user_id=uid, display_name=name, role=role) for uid, name, role in rows]
    home_area = None
    if (
        couple.home_area_label is not None
        and couple.home_lat is not None
        and couple.home_lng is not None
    ):
        home_area = HomeArea(
            label=couple.home_area_label, lat=float(couple.home_lat), lng=float(couple.home_lng)
        )
    return CoupleOut(
        id=couple.id,
        name=couple.name,
        timezone=couple.timezone,
        status=couple.status,
        home_area=home_area,
        members=members,
        partner_joined=any(m.role == "partner" for m in members),
    )


async def get_couple(session: AsyncSession, couple_id: uuid.UUID) -> CoupleOut:
    return await couple_out(session, await _get_couple(session, couple_id))


async def patch_couple(session: AsyncSession, couple_id: uuid.UUID, body: CouplePatch) -> CoupleOut:
    couple = await _get_couple(session, couple_id)
    if body.name is not None:
        couple.name = body.name
    if body.timezone is not None:
        couple.timezone = body.timezone
    await session.flush()
    return await couple_out(session, couple)


async def set_home_area(
    session: AsyncSession, geocoder: Geocoder, couple_id: uuid.UUID, query: str
) -> HomeArea:
    """Geocode `query` and keep only the label, coarse coordinates and place id (NFR-PRIV-1)."""
    couple = await _get_couple(session, couple_id)
    found = await geocoder.geocode(query)
    couple.home_area_label = found.label[:200]
    couple.home_lat = Decimal(str(round(found.lat, 2)))
    couple.home_lng = Decimal(str(round(found.lng, 2)))
    couple.home_place_id = found.place_id
    couple.home_geocoded_at = now()
    await session.flush()
    return HomeArea(
        label=couple.home_area_label, lat=float(couple.home_lat), lng=float(couple.home_lng)
    )


async def _get_constraints(session: AsyncSession, couple_id: uuid.UUID) -> CoupleConstraints:
    row = (
        await session.execute(
            select(CoupleConstraints).where(CoupleConstraints.couple_id == couple_id)
        )
    ).scalar_one_or_none()
    if row is None:
        # Created with the couple, so this only heals an odd database.
        row = CoupleConstraints(
            couple_id=couple_id, currency="USD", transport_modes=[], updated_at=now()
        )
        session.add(row)
        await session.flush()
    return row


async def get_constraints(session: AsyncSession, couple_id: uuid.UUID) -> CoupleConstraints:
    return await _get_constraints(session, couple_id)


async def put_constraints(
    session: AsyncSession, couple_id: uuid.UUID, user_id: uuid.UUID, body: ConstraintsIn
) -> CoupleConstraints:
    row = await _get_constraints(session, couple_id)
    row.budget_min = _money(body.budget_min)
    row.budget_max = _money(body.budget_max)
    row.currency = body.currency
    row.max_travel_minutes = body.max_travel_minutes
    row.max_radius_km = body.max_radius_km
    row.transport_modes = list(body.transport_modes)
    row.alcohol = body.alcohol
    row.kids_pets_notes = body.kids_pets_notes
    row.updated_by_user_id = user_id
    row.updated_at = now()
    await session.flush()
    return row


async def _check_profile(
    session: AsyncSession, couple_id: uuid.UUID, profile_id: uuid.UUID | None
) -> None:
    if profile_id is None:
        return
    found = (
        await session.execute(
            select(PartnerProfile.id).where(
                PartnerProfile.id == profile_id, PartnerProfile.couple_id == couple_id
            )
        )
    ).first()
    if found is None:
        raise ApiError(422, "invalid_profile", "That profile isn't in your couple space.")


async def list_important_dates(session: AsyncSession, couple_id: uuid.UUID) -> list[ImportantDate]:
    rows = await session.execute(
        select(ImportantDate)
        .where(ImportantDate.couple_id == couple_id)
        .order_by(ImportantDate.date, ImportantDate.created_at)
    )
    return list(rows.scalars().all())


async def create_important_date(
    session: AsyncSession, couple_id: uuid.UUID, user_id: uuid.UUID, body: ImportantDateIn
) -> ImportantDate:
    await _check_profile(session, couple_id, body.profile_id)
    row = ImportantDate(
        couple_id=couple_id,
        name=body.name,
        date=body.date,
        recurring=body.recurring,
        kind=body.kind,
        profile_id=body.profile_id,
        created_by_user_id=user_id,
    )
    session.add(row)
    await session.flush()
    return row


async def _get_important_date(
    session: AsyncSession, couple_id: uuid.UUID, date_id: uuid.UUID
) -> ImportantDate:
    row = (
        await session.execute(
            select(ImportantDate).where(
                ImportantDate.id == date_id, ImportantDate.couple_id == couple_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found()
    return row


async def patch_important_date(
    session: AsyncSession, couple_id: uuid.UUID, date_id: uuid.UUID, body: ImportantDatePatch
) -> ImportantDate:
    row = await _get_important_date(session, couple_id, date_id)
    changes = body.model_dump(exclude_unset=True)
    if "profile_id" in changes:
        await _check_profile(session, couple_id, changes["profile_id"])
    for field, value in changes.items():
        setattr(row, field, value)
    await session.flush()
    return row


async def delete_important_date(
    session: AsyncSession, couple_id: uuid.UUID, date_id: uuid.UUID
) -> None:
    await session.delete(await _get_important_date(session, couple_id, date_id))
    await session.flush()
