import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import CurrentUser, current_user
from app.errors import ApiError
from app.models import Couple, CoupleConstraints
from app.providers.places.base import PlacesProvider
from app.providers.registry import get_places
from app.providers.types import LatLng, ProviderUnavailable, is_open_at
from app.schemas.itineraries import PlaceOut
from app.services.couple import not_found, require_couple_id
from app.services.itineraries import zone_for

router = APIRouter(prefix="/places", tags=["places"])

DEFAULT_RADIUS_KM = 15
MAX_RESULTS = 8


@router.get("/search", response_model=list[PlaceOut])
async def search_places(
    q: str = Query(min_length=1, max_length=120),
    open_at: dt.datetime | None = None,
    places: PlacesProvider = Depends(get_places),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> list[PlaceOut]:
    """Venues near the couple's home, for "add a stop". Live provider data, never stored."""
    couple_id = require_couple_id(current.couple_id)
    couple = (
        await session.execute(select(Couple).where(Couple.id == couple_id))
    ).scalar_one_or_none()
    if couple is None:
        raise not_found()
    if couple.home_lat is None or couple.home_lng is None:
        raise ApiError(409, "home_area_required", "Set your home area on the Profile tab first.")
    radius_km = (
        await session.execute(
            select(CoupleConstraints.max_radius_km).where(CoupleConstraints.couple_id == couple_id)
        )
    ).scalar_one_or_none()
    home = LatLng(float(couple.home_lat), float(couple.home_lng))
    try:
        found = await places.search(
            q.strip(),
            home,
            (radius_km or DEFAULT_RADIUS_KM) * 1000,
            open_at=open_at,
            max_results=MAX_RESULTS,
        )
    except ProviderUnavailable:
        raise ApiError(
            503, "provider_unavailable", "Place search isn't available right now."
        ) from None
    local_now = dt.datetime.now(zone_for(couple.timezone)).replace(tzinfo=None)
    return [
        PlaceOut(
            place_id=p.place_id,
            name=p.name,
            address=p.address,
            rating=p.rating,
            price_level=p.price_level,
            lat=p.location.lat if p.location else None,
            lng=p.location.lng if p.location else None,
            open_now=is_open_at(p.opening_periods, local_now),
            attributions=p.attributions,
        )
        for p in found[:MAX_RESULTS]
    ]
