"""Checks on a model-drafted itinerary before it is saved (AI-VAL-1, AI-VAL-2).

`validate_itinerary` returns the problems it finds as `Issue`s; it never raises for a provider
failure and never edits the draft. The planning pipeline does one repair pass with the issue
list and attaches whatever is left as warnings.

Issue codes: ungrounded, unknown_place, unknown_event, evergreen_with_id, closed, tight_travel,
over_budget, event_date, outside_window, bad_times.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.ai.schemas import DraftStop, Issue, ItineraryDraft
from app.providers.places.base import PlacesProvider
from app.providers.types import Event, Place, ProviderUnavailable, is_open_at


@dataclass
class ValidationContext:
    """What the checks compare a draft against.

    `seen_places` / `seen_events` are what the tools returned this run (a place resolved by
    `places.details` is added to `seen_places`). `travel_minutes[i]` is the travel time from
    stop i to stop i+1, None when unknown. Times of `window_start`/`window_end` are aware.
    """

    seen_places: dict[str, Place]
    seen_events: dict[str, Event]
    places: PlacesProvider | None
    timezone: str
    budget_max: Decimal | None
    travel_minutes: list[int | None]
    buffer_minutes: int
    window_start: dt.datetime
    window_end: dt.datetime


async def _resolve_place(ctx: ValidationContext, place_id: str) -> Place | None:
    """The place for an id the model gave: one a tool returned, else one the provider knows."""
    place = ctx.seen_places.get(place_id)
    if place is not None or ctx.places is None:
        return place
    try:
        place = await ctx.places.details(place_id)
    except ProviderUnavailable:
        return None  # down or unknown: unresolvable
    ctx.seen_places[place.place_id] = place
    return place


def _label(stop: DraftStop) -> str:
    return f"'{stop.title}'"


async def validate_itinerary(draft: ItineraryDraft, ctx: ValidationContext) -> list[Issue]:
    try:
        tz: dt.tzinfo = ZoneInfo(ctx.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = dt.UTC
    issues: list[Issue] = []
    stops = draft.stops

    for i, stop in enumerate(stops):
        label = _label(stop)
        if stop.ends_at <= stop.starts_at:
            issues.append(Issue(i, "bad_times", f"{label} ends before it starts."))
        if stop.starts_at < ctx.window_start or stop.ends_at > ctx.window_end:
            issues.append(Issue(i, "outside_window", f"{label} falls outside the date window."))

        place = await _resolve_place(ctx, stop.place_id) if stop.place_id else None
        event = ctx.seen_events.get(stop.event_id) if stop.event_id else None

        if stop.evergreen and (stop.place_id or stop.event_id):
            issues.append(
                Issue(
                    i,
                    "evergreen_with_id",
                    f"{label} is marked evergreen, so it must not carry a place_id or event_id.",
                )
            )
        else:
            if stop.place_id and place is None:
                issues.append(
                    Issue(
                        i,
                        "unknown_place",
                        f"{label} has a place_id that did not come from the tools; use a "
                        "returned place_id.",
                    )
                )
            if stop.event_id and event is None:
                issues.append(
                    Issue(
                        i,
                        "unknown_event",
                        f"{label} has an event_id that did not come from the tools; use a "
                        "returned event_id.",
                    )
                )
            if not stop.evergreen and not stop.place_id and not stop.event_id:
                issues.append(
                    Issue(
                        i,
                        "ungrounded",
                        f"{label} is not a venue or event from the tools; mark it evergreen or "
                        "use a returned place_id/event_id.",
                    )
                )

        if place is not None and place.opening_periods is not None:
            # Probe every 15 minutes plus the last minute, so a midday closure is caught too.
            probes: list[dt.datetime] = []
            probe = stop.starts_at
            while probe < stop.ends_at:
                probes.append(probe)
                probe += dt.timedelta(minutes=15)
            probes.append(stop.ends_at - dt.timedelta(minutes=1))
            if any(
                is_open_at(place.opening_periods, p.astimezone(tz).replace(tzinfo=None)) is False
                for p in probes
            ):
                issues.append(
                    Issue(i, "closed", f"{place.name} is closed for part of {label}'s time.")
                )

        if event is not None and event.local_date != stop.starts_at.astimezone(tz).date():
            issues.append(
                Issue(
                    i,
                    "event_date",
                    f"{event.name} is on {event.local_date.isoformat()}, not on {label}'s date.",
                )
            )

    for i in range(len(stops) - 1):
        here, there = stops[i], stops[i + 1]
        if there.starts_at < here.ends_at:
            issues.append(Issue(i + 1, "bad_times", f"{_label(there)} overlaps {_label(here)}."))
            continue
        travel = ctx.travel_minutes[i] if i < len(ctx.travel_minutes) else None
        if travel is None:
            continue
        gap = (there.starts_at - here.ends_at).total_seconds() / 60
        needed = travel + ctx.buffer_minutes
        if gap < needed:
            issues.append(
                Issue(
                    i + 1,
                    "tight_travel",
                    f"Only {round(gap)} min between {_label(here)} and {_label(there)}; "
                    f"travel takes {travel} min and needs a {ctx.buffer_minutes} min buffer.",
                )
            )

    if ctx.budget_max is not None:
        total = sum((Decimal(str(s.est_cost)) for s in stops if s.est_cost is not None), Decimal(0))
        if total > ctx.budget_max:
            issues.append(
                Issue(
                    None,
                    "over_budget",
                    f"Estimated cost {total} is over the {ctx.budget_max} budget.",
                )
            )
    return issues
