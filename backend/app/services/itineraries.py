"""Itineraries: building the response, editing stops, legs, checklist, scheduling, reactions.

Google terms (m2-ai-plan.md): a stop stores only {"lat", "lng", "fetched_at"} for its place.
Name, address, rating, hours, website and the rest are fetched live by place id every time an
itinerary is built (`itinerary_out`) and never written to the database. Ticketmaster events
and Open-Meteo weather may be stored.

Functions flush but leave the commit to the caller. Callers get the itinerary through
`itinerary_access` first; nothing here checks who is asking except `react`.
"""

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ApiError
from app.models import ChecklistItem, Couple, CoupleMember, Itinerary, Stop, TravelLeg, User
from app.providers.registry import Providers
from app.providers.types import LatLng, Place, ProviderUnavailable, to_jsonable
from app.providers.weather.base import ATTRIBUTION as WEATHER_ATTRIBUTION
from app.realtime import emit_itinerary
from app.schemas.itineraries import (
    ChecklistIn,
    ChecklistItemOut,
    ChecklistPatch,
    ItineraryOut,
    ItineraryPatch,
    ItinerarySummary,
    MapsOut,
    PartnerReaction,
    ReactIn,
    ScheduleIn,
    StopOut,
    StopsPut,
    TravelOut,
    VenueOut,
)
from app.services import reminders, surprise
from app.services.couple import _money, not_found, now
from app.services.feedback import schedule_completion
from app.services.itinerary_access import visible_itineraries
from app.services.notify import notify

logger = logging.getLogger(__name__)

DEFAULT_VENUE_MAX_AGE_DAYS = 30
# Google Places terms: coordinates may be kept at most 30 days, whatever a deployment sets.
MAX_VENUE_MAX_AGE_DAYS = 30
# Our own leg estimate: straight-line distance over the mode's typical speed, plus 3 minutes.
SPEED_KMH = {"walk": 4.5, "bicycle": 14.0, "transit": 20.0, "drive": 30.0, "rideshare": 30.0}
ESTIMATE_OVERHEAD_MIN = 3
SURPRISE_STOP_TITLE = "Surprise stop ✨"
EVENTS_ATTRIBUTION = "Events from Ticketmaster"
GOOGLE_TRAVEL_MODES = {
    "drive": "driving",
    "rideshare": "driving",
    "walk": "walking",
    "bicycle": "bicycling",
    "transit": "transit",
}


# --- Stored venue snapshot (lat/lng only)


def venue_snapshot(location: LatLng) -> dict[str, Any]:
    """The only Google-derived data a stop may keep."""
    return {"lat": location.lat, "lng": location.lng, "fetched_at": now().isoformat()}


def clean_snapshot(venue: Any) -> dict[str, Any] | None:
    """An existing stored venue rebuilt as exactly {lat, lng, fetched_at}, dropping any other
    key. None when it is missing or malformed (no numeric lat/lng, no readable fetched_at)."""
    if not isinstance(venue, dict):
        return None
    lat, lng, fetched_at = venue.get("lat"), venue.get("lng"), venue.get("fetched_at")
    if isinstance(lat, bool) or not isinstance(lat, int | float):
        return None
    if isinstance(lng, bool) or not isinstance(lng, int | float):
        return None
    try:
        datetime.fromisoformat(str(fetched_at))
    except ValueError:
        return None
    return {"lat": float(lat), "lng": float(lng), "fetched_at": str(fetched_at)}


def estimate_leg(origin: LatLng, dest: LatLng, mode: str) -> tuple[int, int]:
    """Our own (duration_minutes, distance_m) between two points. Never a Routes result."""
    meters = origin.distance_m(dest)
    speed = SPEED_KMH.get(mode, SPEED_KMH["drive"])
    minutes = math.ceil(meters / 1000 / speed * 60) + ESTIMATE_OVERHEAD_MIN
    return minutes, round(meters)


def _stored_coords(stop: Stop) -> LatLng | None:
    venue = stop.venue
    if not venue or venue.get("lat") is None or venue.get("lng") is None:
        return None
    return LatLng(float(venue["lat"]), float(venue["lng"]))


def _snapshot_stale(venue: dict[str, Any] | None, max_age: timedelta) -> bool:
    if not venue:
        return True
    try:
        fetched = datetime.fromisoformat(str(venue["fetched_at"]))
    except (KeyError, ValueError):
        return True
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=UTC)
    return now() - fetched > max_age


# --- Loading


async def load_stops(session: AsyncSession, itinerary_id: uuid.UUID) -> list[Stop]:
    rows = await session.execute(
        select(Stop).where(Stop.itinerary_id == itinerary_id).order_by(Stop.position)
    )
    return list(rows.scalars().all())


async def load_legs(session: AsyncSession, itinerary_id: uuid.UUID) -> list[TravelLeg]:
    rows = await session.execute(select(TravelLeg).where(TravelLeg.itinerary_id == itinerary_id))
    return list(rows.scalars().all())


async def load_checklist(session: AsyncSession, itinerary_id: uuid.UUID) -> list[ChecklistItem]:
    rows = await session.execute(
        select(ChecklistItem)
        .where(ChecklistItem.itinerary_id == itinerary_id)
        .order_by(ChecklistItem.position, ChecklistItem.created_at)
    )
    return list(rows.scalars().all())


# --- Map links (FR-ITIN-6)


def _pair(coords: LatLng) -> str:
    return f"{coords.lat},{coords.lng}"


def maps_links(
    *,
    title: str,
    name: str | None,
    coords: LatLng | None,
    place_id: str | None,
    previous: tuple[LatLng, str | None] | None,
    mode: str | None,
) -> MapsOut:
    """Apple and Google Maps links for one stop, and Google directions from the previous one.
    No coordinates, no links."""
    if coords is None:
        return MapsOut()
    google_query = quote(name, safe="") if name else _pair(coords)
    google = f"https://www.google.com/maps/search/?api=1&query={google_query}"
    if place_id:
        google += f"&query_place_id={quote(place_id, safe='')}"
    apple_name = quote(name or title, safe="")
    apple = f"https://maps.apple.com/place?coordinate={_pair(coords)}&name={apple_name}"
    directions = None
    if previous is not None:
        origin, origin_place_id = previous
        directions = (
            f"https://www.google.com/maps/dir/?api=1&origin={_pair(origin)}"
            + (f"&origin_place_id={quote(origin_place_id, safe='')}" if origin_place_id else "")
            + f"&destination={_pair(coords)}"
            + (f"&destination_place_id={quote(place_id, safe='')}" if place_id else "")
            + f"&travelmode={GOOGLE_TRAVEL_MODES.get(mode or 'drive', 'driving')}"
        )
    return MapsOut(apple=apple, google=google, directions_from_previous=directions)


# --- Response


async def _live_place(providers: Providers, place_id: str) -> Place | None:
    """Live details for a place, or None when places are unavailable or the lookup fails."""
    if providers.places is None:
        return None
    try:
        return await providers.places.details(place_id)
    except ProviderUnavailable:
        return None


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


async def _leg_out(
    providers: Providers,
    leg: TravelLeg,
    origin: LatLng | None,
    dest: LatLng | None,
    depart_at: datetime,
) -> TravelOut:
    """One leg for the response: live from the routes provider when it answers, else the stored
    estimate. Routes results are shown, never stored."""
    if providers.routes is not None and origin is not None and dest is not None:
        try:
            live = await providers.routes.travel(origin, dest, leg.mode, depart_at)
        except ProviderUnavailable:
            pass
        else:
            return TravelOut(
                mode=leg.mode,
                duration_minutes=live.duration_minutes,
                distance_m=live.distance_m,
                source="live",
            )
    return TravelOut(
        mode=leg.mode,
        duration_minutes=leg.duration_minutes,
        distance_m=leg.distance_m,
        source="estimate",
    )


def _stop_hidden(stop: Stop, redact_before: datetime | None) -> bool:
    """Whether `stop` is still a surprise to a non-planner viewer; `redact_before` is "now", or
    None for the planner, who always sees everything."""
    return (
        redact_before is not None
        and stop.reveal_policy == "at_stop_time"
        and stop.starts_at is not None
        and stop.starts_at > redact_before
    )


def _visible_warnings(warnings: list[Any] | None, hidden: list[bool]) -> list[Any]:
    """The itinerary's warnings. Once any stop is redacted only those pointing at a stop that
    isn't are kept: the rest could carry a hidden stop's title or cost."""
    if not any(hidden):
        return list(warnings or [])
    return [
        warning
        for warning in warnings or []
        if isinstance(warning, dict)
        and isinstance(warning.get("stop_index"), int)
        and 0 <= warning["stop_index"] < len(hidden)
        and not hidden[warning["stop_index"]]
    ]


def _redacted_stop(stop: Stop) -> StopOut:
    return StopOut(
        id=stop.id,
        position=stop.position,
        title=SURPRISE_STOP_TITLE,
        starts_at=stop.starts_at,
        ends_at=stop.ends_at,
        description=None,
        est_cost=None,
        notes=None,
        place_id=None,
        event=None,
        reveal_policy=stop.reveal_policy,
        warnings=[],
        booking_url=None,
        evergreen=False,
        venue=None,
        travel_to_next=None,
        maps=MapsOut(),
    )


async def itinerary_out(
    session: AsyncSession,
    itinerary: Itinerary,
    viewer_user_id: uuid.UUID,
    providers: Providers,
    *,
    settings: Settings | None = None,
) -> ItineraryOut:
    """The full itinerary for `viewer_user_id`, venue details fetched live. A stop whose
    place lookup fails still comes back, with only its stored coordinates. A stale stored
    snapshot is refreshed (flushed, not committed) whenever a lookup succeeds. Each leg's
    duration and distance are fetched live (source "live"), falling back to the stored
    estimate (source "estimate"); a live result is never written.

    For a viewer who isn't the planner, an upcoming `at_stop_time` stop is redacted (m4 design,
    "Per-stop reveal"): no place lookup is made for it, and nothing about it, the leg into it
    or the directions out of it reaches the response."""
    max_age = timedelta(
        days=min(
            settings.venue_snapshot_max_age_days if settings else DEFAULT_VENUE_MAX_AGE_DAYS,
            MAX_VENUE_MAX_AGE_DAYS,
        )
    )
    stops = await load_stops(session, itinerary.id)
    redact_before = None if itinerary.planner_user_id == viewer_user_id else now()
    hidden = [_stop_hidden(stop, redact_before) for stop in stops]
    legs = {leg.from_stop_id: leg for leg in await load_legs(session, itinerary.id)}
    checklist = await load_checklist(session, itinerary.id)

    attributions: list[str] = []

    def attribute(text: str) -> None:
        if text not in attributions:
            attributions.append(text)

    stop_outs: list[StopOut] = []
    stop_coords: list[LatLng | None] = []
    previous: tuple[LatLng, str | None] | None = None
    previous_leg: TravelLeg | None = None
    refreshed = False
    for stop, is_hidden in zip(stops, hidden, strict=True):
        if is_hidden:
            stop_outs.append(_redacted_stop(stop))
            previous = None
            previous_leg = None
            stop_coords.append(None)
            continue
        place = await _live_place(providers, stop.place_id) if stop.place_id else None
        coords = _stored_coords(stop)
        if place is not None:
            for text in place.attributions:
                attribute(text)
            if place.location is not None:
                coords = place.location
                if _snapshot_stale(stop.venue, max_age):
                    stop.venue = venue_snapshot(place.location)
                    refreshed = True
        venue: VenueOut | None = None
        if place is not None:
            venue = VenueOut(
                name=place.name,
                address=place.address,
                lat=coords.lat if coords else None,
                lng=coords.lng if coords else None,
                rating=place.rating,
                price_level=place.price_level,
                website=place.website,
                maps_uri=place.maps_uri,
            )
        elif coords is not None:
            venue = VenueOut(lat=coords.lat, lng=coords.lng)
        event = stop.event or None
        booking_url = (place.website if place is not None else None) or (
            event.get("url") if event else None
        )
        leg = legs.get(stop.id)
        stop_outs.append(
            StopOut(
                id=stop.id,
                position=stop.position,
                title=stop.title,
                starts_at=stop.starts_at,
                ends_at=stop.ends_at,
                description=stop.description,
                est_cost=_float(stop.est_cost),
                notes=stop.notes,
                place_id=stop.place_id,
                event=event,
                reveal_policy=stop.reveal_policy,
                warnings=list(stop.warnings or []),
                booking_url=booking_url,
                evergreen=stop.evergreen,
                venue=venue,
                travel_to_next=None,
                maps=maps_links(
                    title=stop.title,
                    name=place.name if place is not None else None,
                    coords=coords,
                    place_id=stop.place_id,
                    previous=previous,
                    mode=previous_leg.mode if previous_leg else None,
                ),
            )
        )
        previous = (coords, stop.place_id) if coords is not None else None
        previous_leg = leg
        stop_coords.append(coords)
    if refreshed:
        await session.flush()

    for index, stop in enumerate(stops[:-1]):
        leg = legs.get(stop.id)
        if leg is not None and not hidden[index] and not hidden[index + 1]:
            depart_at = stop.ends_at or stop.starts_at or itinerary.starts_at or now()
            stop_outs[index].travel_to_next = await _leg_out(
                providers, leg, stop_coords[index], stop_coords[index + 1], depart_at
            )

    if itinerary.weather:
        attribute(str(itinerary.weather.get("attribution") or WEATHER_ATTRIBUTION))
    if any(stop.event for stop, is_hidden in zip(stops, hidden, strict=True) if not is_hidden):
        attribute(EVENTS_ATTRIBUTION)

    return ItineraryOut(
        id=itinerary.id,
        idea_id=itinerary.idea_id,
        planner_user_id=itinerary.planner_user_id,
        is_planner=itinerary.planner_user_id == viewer_user_id,
        title=itinerary.title,
        starts_at=itinerary.starts_at,
        ends_at=itinerary.ends_at,
        status=itinerary.status,
        announced_at=itinerary.announced_at,
        invitation_message=itinerary.invitation_message,
        is_surprise=itinerary.is_surprise,
        reveal_at=itinerary.reveal_at,
        revealed_at=itinerary.revealed_at,
        budget=_float(itinerary.budget),
        est_total=None if any(hidden) else _float(itinerary.est_total),
        currency=itinerary.currency,
        weather=itinerary.weather,
        backup_plan=itinerary.backup_plan,
        bring_wear=itinerary.bring_wear,
        warnings=_visible_warnings(itinerary.warnings, hidden),
        partner_reaction=(
            PartnerReaction.model_validate(itinerary.partner_reaction)
            if itinerary.partner_reaction
            else None
        ),
        seen_by_partner_at=itinerary.seen_by_partner_at,
        created_at=itinerary.created_at,
        updated_at=itinerary.updated_at,
        stops=stop_outs,
        has_evergreen=any(stop.evergreen for stop in stop_outs),
        checklist=[_checklist_out(item) for item in checklist],
        attributions=attributions,
    )


# --- List


async def list_summaries(
    session: AsyncSession,
    viewer_user_id: uuid.UUID,
    couple_id: uuid.UUID,
    *,
    status: str | None,
    starts_from: datetime | None,
    starts_to: datetime | None,
) -> list[ItinerarySummary]:
    query = visible_itineraries(session, viewer_user_id, couple_id)
    if status is not None:
        query = query.where(Itinerary.status == status)
    if starts_from is not None:
        query = query.where(Itinerary.starts_at >= starts_from)
    if starts_to is not None:
        query = query.where(Itinerary.starts_at <= starts_to)
    result = await session.execute(
        query.order_by(Itinerary.starts_at.asc().nulls_last(), Itinerary.created_at)
    )
    rows = list(result.scalars().all())
    counts: dict[uuid.UUID, int] = {}
    if rows:
        counted = await session.execute(
            select(Stop.itinerary_id, func.count())
            .where(Stop.itinerary_id.in_([r.id for r in rows]))
            .group_by(Stop.itinerary_id)
        )
        counts = {itinerary_id: n for itinerary_id, n in counted.all()}
    return [
        ItinerarySummary(
            id=r.id,
            title=r.title,
            starts_at=r.starts_at,
            ends_at=r.ends_at,
            status=r.status,
            is_surprise=r.is_surprise,
            announced=r.announced_at is not None,
            is_planner=r.planner_user_id == viewer_user_id,
            stop_count=counts.get(r.id, 0),
        )
        for r in rows
    ]


async def mark_seen(session: AsyncSession, itinerary: Itinerary, viewer_user_id: uuid.UUID) -> None:
    """The other member opening an announced date sets `seen_by_partner_at` (FR-SURP-1)."""
    if (
        itinerary.planner_user_id != viewer_user_id
        and itinerary.announced_at is not None
        and itinerary.seen_by_partner_at is None
    ):
        itinerary.seen_by_partner_at = now()
        await session.flush()


# --- Revalidation (no AI)


async def compute_warnings(
    session: AsyncSession, itinerary: Itinerary, providers: Providers, settings: Settings
) -> list[dict[str, Any]]:
    """The itinerary's current warnings. The seam where hours/weather/event validation plugs
    in; for now it leaves them as they are."""
    return list(itinerary.warnings or [])


async def _partner_id(session: AsyncSession, itinerary: Itinerary) -> uuid.UUID | None:
    """The active member who isn't the planner."""
    return (
        await session.execute(
            select(CoupleMember.user_id).where(
                CoupleMember.couple_id == itinerary.couple_id,
                CoupleMember.user_id != itinerary.planner_user_id,
                CoupleMember.left_at.is_(None),
            )
        )
    ).scalar_one_or_none()


def _partner_knows(itinerary: Itinerary) -> bool:
    """Whether the non-planner member has been told of this date: it was announced, or (for a
    surprise) revealed. An unrevealed surprise is still a secret."""
    if itinerary.is_surprise:
        return itinerary.revealed_at is not None
    return itinerary.announced_at is not None


async def on_scheduled(
    session: AsyncSession,
    itinerary: Itinerary,
    settings: Settings,
    prev_status: str | None = None,
    prev_starts_at: datetime | None = None,
    prev_known: bool = False,
) -> None:
    """Notify the non-planner member after an announced date is scheduled, or moved when the
    partner already knew of it at another time (`prev_*` is the state before scheduling; a
    revealed surprise counts as known)."""
    if itinerary.starts_at is None:
        return
    # Switching a known date to an unrevealed surprise makes it a secret again: stay silent.
    known = prev_status == "scheduled" and prev_known and _partner_knows(itinerary)
    if known and prev_starts_at == itinerary.starts_at:
        return  # the partner already has this date at this time
    if not known and (itinerary.announced_at is None or itinerary.is_surprise):
        return
    couple = await session.get(Couple, itinerary.couple_id)
    if couple is None:
        return
    partner = await _partner_id(session, itinerary)
    if partner is None:
        return
    local = itinerary.starts_at.astimezone(zone_for(couple.timezone))
    await notify(
        session,
        partner,
        "date_rescheduled" if known else "date_announced",
        "Your date moved 📅" if known else "You've got a date 📅",
        f"{itinerary.title} — {reminders.local_label(local)}",
        {"url": f"/itineraries/{itinerary.id}"},
        couple_id=itinerary.couple_id,
        dedupe_key=(
            f"{'rescheduled' if known else 'announced'}:{itinerary.id}:"
            f"{itinerary.starts_at.isoformat()}"
        ),
        settings=settings,
    )


def zone_for(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


async def refresh_weather(
    session: AsyncSession, itinerary: Itinerary, providers: Providers
) -> None:
    """Re-fetch the forecast for the itinerary's window at the couple's home. Best effort: if
    the provider is down the stored weather stays; a window beyond the forecast clears it."""
    if providers.weather is None or itinerary.starts_at is None:
        return
    couple = await session.get(Couple, itinerary.couple_id)
    if couple is None or couple.home_lat is None or couple.home_lng is None:
        return
    zone = zone_for(couple.timezone)
    start = itinerary.starts_at.astimezone(zone)
    end = (itinerary.ends_at or itinerary.starts_at + timedelta(hours=3)).astimezone(zone)
    try:
        forecast = await providers.weather.forecast(
            LatLng(float(couple.home_lat), float(couple.home_lng)), start, end
        )
    except ProviderUnavailable:
        return
    itinerary.weather = to_jsonable(forecast) if forecast.hourly else None


@dataclass(frozen=True)
class LegSnapshot:
    from_stop_id: uuid.UUID
    to_stop_id: uuid.UUID
    mode: str
    duration_minutes: int
    distance_m: int | None


async def snapshot_legs(session: AsyncSession, itinerary_id: uuid.UUID) -> list[LegSnapshot]:
    """The current legs as plain values, for a caller about to delete stops (which cascades)."""
    return [
        LegSnapshot(
            leg.from_stop_id, leg.to_stop_id, leg.mode, leg.duration_minutes, leg.distance_m
        )
        for leg in await load_legs(session, itinerary_id)
    ]


async def recompute_legs(
    session: AsyncSession,
    itinerary: Itinerary,
    stops: list[Stop],
    old_legs: list[LegSnapshot] | None = None,
) -> None:
    """Replace the travel legs between consecutive stops that both have coordinates. The mode
    comes from the leg that left the same stop (else the first existing leg's, else "drive").
    Each stored leg is our own estimate (`estimate_leg`) from the stops' stored coordinates;
    Routes results are never persisted (they are fetched live when the itinerary is built).
    `old_legs` is what the legs were before the caller changed the stops (default: now)."""
    if old_legs is None:
        old_legs = await snapshot_legs(session, itinerary.id)
    mode_from = {leg.from_stop_id: leg.mode for leg in old_legs}
    default_mode = old_legs[0].mode if old_legs else "drive"
    await session.execute(delete(TravelLeg).where(TravelLeg.itinerary_id == itinerary.id))
    for first, second in zip(stops, stops[1:], strict=False):
        origin, dest = _stored_coords(first), _stored_coords(second)
        if origin is None or dest is None:
            continue
        mode = mode_from.get(first.id, default_mode)
        duration_minutes, distance_m = estimate_leg(origin, dest, mode)
        session.add(
            TravelLeg(
                itinerary_id=itinerary.id,
                from_stop_id=first.id,
                to_stop_id=second.id,
                mode=mode,
                duration_minutes=duration_minutes,
                distance_m=distance_m,
            )
        )
    await session.flush()


async def revalidate(
    session: AsyncSession, itinerary: Itinerary, providers: Providers, settings: Settings
) -> None:
    """Re-check a date after its times changed: travel legs, weather, warnings (no AI)."""
    stops = await load_stops(session, itinerary.id)
    await recompute_legs(session, itinerary, stops)
    await refresh_weather(session, itinerary, providers)
    itinerary.warnings = await compute_warnings(session, itinerary, providers, settings)
    itinerary.updated_at = now()
    await session.flush()


# --- Edits


def _invalid_times() -> ApiError:
    return ApiError(422, "invalid_times", "The end can't be before the start.")


async def patch_itinerary(
    session: AsyncSession,
    itinerary: Itinerary,
    body: ItineraryPatch,
    providers: Providers,
    settings: Settings,
) -> None:
    await session.refresh(itinerary, with_for_update=True)  # serialise with completion/cancel
    fields = body.model_fields_set
    starts_at = body.starts_at if "starts_at" in fields else itinerary.starts_at
    ends_at = body.ends_at if "ends_at" in fields else itinerary.ends_at
    if starts_at is not None and ends_at is not None and ends_at < starts_at:
        raise _invalid_times()
    if starts_at is None and itinerary.status == "scheduled":
        raise ApiError(422, "invalid_times", "A scheduled date needs a start time.")
    times_changed = starts_at != itinerary.starts_at or ends_at != itinerary.ends_at
    if times_changed and itinerary.status in ("completed", "cancelled"):
        raise ApiError(409, "not_editable", "A finished date's time can't be changed.")
    prev_status, prev_starts_at = itinerary.status, itinerary.starts_at
    prev_known = _partner_knows(itinerary)
    if "title" in fields:
        itinerary.title = body.title
    itinerary.starts_at = starts_at
    itinerary.ends_at = ends_at
    if "budget" in fields:
        itinerary.budget = _money(body.budget)
    for name in ("backup_plan", "bring_wear", "invitation_message"):
        if name in fields:
            setattr(itinerary, name, getattr(body, name))
    itinerary.updated_at = now()
    await session.flush()
    if times_changed and itinerary.status == "scheduled":
        await revalidate(session, itinerary, providers, settings)
        await schedule_completion(session, itinerary)
        await on_scheduled(session, itinerary, settings, prev_status, prev_starts_at, prev_known)
        await reminders.schedule_reminders(session, itinerary)
    emit_itinerary(session, itinerary, "itinerary.updated")


async def replace_stops(
    session: AsyncSession,
    itinerary: Itinerary,
    body: StopsPut,
    providers: Providers,
    settings: Settings,
) -> None:
    """Replace all stops, in the given order (FR-ITIN-5). A new or changed place id gets a
    fresh {lat, lng, fetched_at} snapshot from a live lookup (503 if it can't be made)."""
    existing = {stop.id: stop for stop in await load_stops(session, itinerary.id)}
    old_legs = await snapshot_legs(session, itinerary.id)
    ids = [s.id for s in body.stops if s.id is not None]
    if len(set(ids)) != len(ids):
        raise ApiError(422, "invalid_stops", "A stop appears twice.")
    if any(i not in existing for i in ids):
        raise ApiError(422, "invalid_stops", "Some stops aren't part of this date.")

    # Look every new place up first, so a provider failure leaves nothing half-changed.
    venues: list[dict[str, Any] | None] = []
    for item in body.stops:
        current = existing[item.id] if item.id is not None else None
        if item.place_id is None:
            # An event stop keeps its stored venue; a stop that lost its place loses its venue.
            venues.append(
                clean_snapshot(current.venue) if current and current.place_id is None else None
            )
        elif (
            current
            and current.place_id == item.place_id
            and (kept_venue := clean_snapshot(current.venue)) is not None
        ):
            venues.append(kept_venue)
        else:
            venues.append(await _lookup_snapshot(providers, item.place_id))

    for stop_id, old in existing.items():
        if stop_id not in ids:
            await session.delete(old)
    kept: list[Stop] = []
    for index, item in enumerate(body.stops):
        stop = existing[item.id] if item.id is not None else Stop(itinerary_id=itinerary.id)
        if item.id is None:
            session.add(stop)
        stop.position = index
        stop.title = item.title
        stop.place_id = item.place_id
        stop.venue = venues[index]
        stop.starts_at = item.starts_at
        stop.ends_at = item.ends_at
        stop.description = item.description
        stop.est_cost = _money(item.est_cost)
        stop.notes = item.notes
        stop.reveal_policy = item.reveal_policy
        kept.append(stop)
    await session.flush()

    costs = [s.est_cost for s in kept if s.est_cost is not None]
    itinerary.est_total = sum(costs, Decimal("0")) if costs else None
    await recompute_legs(session, itinerary, kept, old_legs)
    itinerary.warnings = await compute_warnings(session, itinerary, providers, settings)
    itinerary.updated_at = now()
    await session.flush()
    emit_itinerary(session, itinerary, "itinerary.updated")


async def _lookup_snapshot(providers: Providers, place_id: str) -> dict[str, Any] | None:
    unavailable = ApiError(503, "provider_unavailable", "Place lookup isn't available right now.")
    if providers.places is None:
        raise unavailable
    try:
        place = await providers.places.details(place_id)
    except ProviderUnavailable:
        raise unavailable from None
    return venue_snapshot(place.location) if place.location is not None else None


# --- Checklist (FR-PLAN-6)


def _checklist_out(item: ChecklistItem) -> ChecklistItemOut:
    return ChecklistItemOut(
        id=item.id,
        position=item.position,
        text=item.text,
        kind=item.kind,
        due_at=item.due_at,
        link=item.link,
        done=item.done_at is not None,
        done_at=item.done_at,
    )


async def add_checklist_item(
    session: AsyncSession, itinerary: Itinerary, body: ChecklistIn
) -> ChecklistItemOut:
    last = (
        await session.execute(
            select(func.max(ChecklistItem.position)).where(
                ChecklistItem.itinerary_id == itinerary.id
            )
        )
    ).scalar_one()
    item = ChecklistItem(
        itinerary_id=itinerary.id,
        position=0 if last is None else last + 1,
        text=body.text,
        kind=body.kind,
        due_at=body.due_at,
        link=body.link,
    )
    session.add(item)
    await session.flush()
    await reminders.schedule_checklist_due(session, itinerary, item)
    emit_itinerary(session, itinerary, "itinerary.updated")
    return _checklist_out(item)


async def _get_item(
    session: AsyncSession, itinerary: Itinerary, item_id: uuid.UUID
) -> ChecklistItem:
    item = (
        await session.execute(
            select(ChecklistItem).where(
                ChecklistItem.id == item_id, ChecklistItem.itinerary_id == itinerary.id
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise not_found("That checklist item isn't here.")
    return item


async def patch_checklist_item(
    session: AsyncSession, itinerary: Itinerary, item_id: uuid.UUID, body: ChecklistPatch
) -> ChecklistItemOut:
    item = await _get_item(session, itinerary, item_id)
    was_done = item.done_at is not None
    fields = body.model_fields_set
    if "text" in fields:
        item.text = body.text
    if "kind" in fields:
        item.kind = body.kind
    if "due_at" in fields:
        item.due_at = body.due_at
    if "link" in fields:
        item.link = body.link
    if "done" in fields:
        if not body.done:
            item.done_at = None
        elif item.done_at is None:
            item.done_at = now()
    await session.flush()
    # A new due time needs a nudge, and so does an item unticked after its date was scheduled
    # (scheduling skips ticked items); the dedupe key stops a double.
    if "due_at" in fields or (was_done and item.done_at is None):
        await reminders.schedule_checklist_due(session, itinerary, item)
    emit_itinerary(session, itinerary, "itinerary.updated")
    return _checklist_out(item)


async def delete_checklist_item(
    session: AsyncSession, itinerary: Itinerary, item_id: uuid.UUID
) -> None:
    await session.delete(await _get_item(session, itinerary, item_id))
    await session.flush()
    emit_itinerary(session, itinerary, "itinerary.updated")


# --- Schedule, cancel, react


async def schedule(
    session: AsyncSession,
    itinerary: Itinerary,
    body: ScheduleIn,
    providers: Providers,
    settings: Settings,
) -> None:
    """Announce or surprise (FR-CAL-3/5, FR-PLAN-7); from draft or scheduled."""
    await session.refresh(itinerary, with_for_update=True)  # serialise with the completion job
    if itinerary.status not in ("draft", "scheduled"):
        raise ApiError(409, "invalid_status", f"A {itinerary.status} date can't be scheduled.")
    ends_at = body.ends_at
    if ends_at is None and itinerary.ends_at is not None and itinerary.ends_at >= body.starts_at:
        ends_at = itinerary.ends_at
    if ends_at is not None and ends_at < body.starts_at:
        raise _invalid_times()
    if body.mode == "surprise" and itinerary.seen_by_partner_at is not None:
        raise ApiError(409, "already_seen", "Your partner has already seen this date.")
    prev_status, prev_starts_at = itinerary.status, itinerary.starts_at
    prev_known = _partner_knows(itinerary)
    itinerary.starts_at = body.starts_at
    itinerary.ends_at = ends_at
    if body.invitation_message is not None:
        itinerary.invitation_message = body.invitation_message
    if body.mode == "announce":
        itinerary.is_surprise = False
        itinerary.reveal_at = None
        if itinerary.announced_at is None:
            itinerary.announced_at = now()
    else:
        itinerary.is_surprise = True
        itinerary.reveal_at = body.starts_at
    itinerary.status = "scheduled"
    itinerary.updated_at = now()
    await session.flush()
    await revalidate(session, itinerary, providers, settings)
    await schedule_completion(session, itinerary)
    await on_scheduled(session, itinerary, settings, prev_status, prev_starts_at, prev_known)
    if itinerary.is_surprise:
        moment = now()
        await surprise.process_due(session, itinerary, settings, moment)
        await surprise.schedule_due_jobs(session, itinerary, moment)
    emit_itinerary(session, itinerary, "date.scheduled")
    await reminders.schedule_reminders(session, itinerary)


async def cancel(session: AsyncSession, itinerary: Itinerary, settings: Settings) -> None:
    await session.refresh(itinerary, with_for_update=True)  # serialise with the completion job
    if itinerary.status == "completed":
        raise ApiError(409, "invalid_status", "A completed date can't be cancelled.")
    # An unrevealed surprise never told the surprisee anything, so cancelling it tells them
    # nothing; a revealed one they know about.
    tell_partner = itinerary.status == "scheduled" and _partner_knows(itinerary)
    itinerary.status = "cancelled"
    itinerary.updated_at = now()
    await session.flush()
    if tell_partner:
        partner = await _partner_id(session, itinerary)
        if partner is not None:
            await notify(
                session,
                partner,
                "date_cancelled",
                "Date cancelled",
                itinerary.title,
                {"url": f"/itineraries/{itinerary.id}"},
                couple_id=itinerary.couple_id,
                dedupe_key=f"cancelled:{itinerary.id}",
                settings=settings,
            )
    emit_itinerary(session, itinerary, "date.cancelled")


async def react(
    session: AsyncSession,
    itinerary: Itinerary,
    viewer_user_id: uuid.UUID,
    body: ReactIn,
    settings: Settings,
) -> None:
    """The partner's heart or "can we move it?" (FR-CAL-5). The planner can't react."""
    if itinerary.planner_user_id == viewer_user_id:
        raise ApiError(403, "forbidden", "You planned this date; only your partner can react.")
    at = now().isoformat()
    itinerary.partner_reaction = {
        "kind": body.kind,
        "suggested_start": body.suggested_start.isoformat() if body.suggested_start else None,
        "note": body.note or None,
        "at": at,
    }
    itinerary.updated_at = now()
    await session.flush()
    viewer = await session.get(User, viewer_user_id)
    name = (viewer.display_name if viewer is not None else None) or "Your partner"
    await notify(
        session,
        itinerary.planner_user_id,
        "partner_reaction",
        f"❤️ {name} loved the plan" if body.kind == "heart" else f"{name} asked to move the date",
        body.note or itinerary.title,
        {"url": f"/itineraries/{itinerary.id}"},
        couple_id=itinerary.couple_id,
        dedupe_key=f"reaction:{itinerary.id}:{at}",
        settings=settings,
    )
    emit_itinerary(session, itinerary, "itinerary.updated")
