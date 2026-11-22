"""The couple context handed to the model with every run (AI-6): compact JSON.

Privacy rules (NFR-PRIV-4, FR-PROF-8): partners appear by first name only ("Partner" when they
have no name), the home area is a label plus 2-dp coordinates, and the only planner notes in it
are the requester's own notes about the recipient, never anyone else's.
"""

import datetime as dt
import json
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import exists, false, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.errors import ApiError
from app.models import (
    Couple,
    CoupleConstraints,
    Idea,
    ImportantDate,
    PartnerProfile,
    PlannerNote,
    TasteSummary,
    Vote,
)

OCCASION_WINDOW_DAYS = 60
RECENT_IDEAS = 15
ACTIVE_IDEAS = 25
PASSED_IDEAS = 25

_PROFILE_FIELDS = (
    "liked_categories",
    "disliked_categories",
    "cuisines_liked",
    "cuisines_disliked",
    "energy",
    "indoor_outdoor",
    "social_setting",
    "loves",
)
_HARD_CONSTRAINT_FIELDS = ("dietary", "allergies", "accessibility", "hard_nos")


def first_name(display_name: str | None) -> str:
    parts = (display_name or "").split()
    return parts[0] if parts else "Partner"


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _profile(profile: PartnerProfile) -> dict[str, Any]:
    out: dict[str, Any] = {"name": first_name(profile.display_name)}
    out.update({k: getattr(profile, k) for k in _PROFILE_FIELDS if _present(getattr(profile, k))})
    hard = {
        k: getattr(profile, k) for k in _HARD_CONSTRAINT_FIELDS if _present(getattr(profile, k))
    }
    if hard:
        out["hard_constraints"] = hard
    return out


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _constraints(row: CoupleConstraints | None) -> dict[str, Any]:
    if row is None:
        return {}
    values = {
        "budget_min": _number(row.budget_min),
        "budget_max": _number(row.budget_max),
        "currency": row.currency,
        "max_travel_minutes": row.max_travel_minutes,
        "max_radius_km": row.max_radius_km,
        "transport_modes": row.transport_modes,
        "alcohol": row.alcohol,
        "kids_pets_notes": row.kids_pets_notes,
    }
    return {k: v for k, v in values.items() if _present(v)}


def _next_occurrence(day: dt.date, recurring: bool, today: dt.date) -> dt.date | None:
    if not recurring:
        return day if day >= today else None
    for year in (today.year, today.year + 1):
        try:
            candidate = day.replace(year=year)
        except ValueError:  # 29 February in a non-leap year
            candidate = dt.date(year, 3, 1)
        if candidate >= today:
            return candidate
    return None


def _occasions(rows: list[ImportantDate], today: dt.date) -> list[dict[str, Any]]:
    found: list[tuple[dt.date, ImportantDate]] = []
    for row in rows:
        when = _next_occurrence(row.date, row.recurring, today)
        if when is not None and (when - today).days <= OCCASION_WINDOW_DAYS:
            found.append((when, row))
    found.sort(key=lambda pair: pair[0])
    return [
        {
            "name": row.name,
            "kind": row.kind,
            "date": when.isoformat(),
            "days_away": (when - today).days,
        }
        for when, row in found
    ]


async def build_context(
    session: AsyncSession,
    couple_id: uuid.UUID,
    requester_user_id: uuid.UUID,
    recipient_profile_id: uuid.UUID,
    *,
    exclude_surprises_for: uuid.UUID | None = None,
    shared: bool = False,
) -> str:
    """The context for a run planned by `requester_user_id` for `recipient_profile_id`.

    The requester's idea list never includes another member's `is_surprise` idea, whatever the
    caller passes. `exclude_surprises_for` is still accepted for callers that pass it, but the rule
    no longer needs it.

    `shared` is for a reply both partners will read (the shared chat thread): the planner notes
    are left out entirely, as is every surprise (the requester's own included), anyone's private
    idea, how the members voted and the recipient's taste summary.
    """
    couple = await session.get(Couple, couple_id)
    profiles = list(
        (await session.execute(select(PartnerProfile).where(PartnerProfile.couple_id == couple_id)))
        .scalars()
        .all()
    )
    recipient = next((p for p in profiles if p.id == recipient_profile_id), None)
    if couple is None or recipient is None:
        raise ApiError(404, "not_found", "Not found.")
    planner = next((p for p in profiles if p.id != recipient.id), None)

    try:
        tz: dt.tzinfo = ZoneInfo(couple.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = dt.UTC
    today = dt.datetime.now(tz).date()

    constraints = (
        await session.execute(
            select(CoupleConstraints).where(CoupleConstraints.couple_id == couple_id)
        )
    ).scalar_one_or_none()
    dates = list(
        (await session.execute(select(ImportantDate).where(ImportantDate.couple_id == couple_id)))
        .scalars()
        .all()
    )
    # Only the requester's own notes. Filtering on the author here, in the query, is what keeps
    # the partner's notes out of the model's hands.
    notes: Sequence[Any] = (
        []
        if shared
        else (
            await session.execute(
                select(PlannerNote.kind, PlannerNote.text)
                .where(
                    PlannerNote.couple_id == couple_id,
                    PlannerNote.author_user_id == requester_user_id,
                    PlannerNote.about_profile_id == recipient.id,
                    PlannerNote.archived_at.is_(None),
                )
                .order_by(PlannerNote.created_at)
            )
        ).all()
    )
    # It is derived partly from the other partner's private planner reads, so a shared reply
    # does not get it.
    taste = (
        None
        if shared
        else (
            await session.execute(
                select(TasteSummary.summary).where(TasteSummary.about_profile_id == recipient.id)
            )
        ).scalar_one_or_none()
    )

    # Ideas the requester can see: the shared deck plus their own private ones, and no one
    # else's surprise. A shared reply is read by both, so it gets the shared deck only, less
    # every surprise.
    if shared:
        visible = (Idea.visibility == "shared") & (Idea.is_surprise == false())
    else:
        visible = (
            (Idea.visibility == "shared") | (Idea.created_by_user_id == requester_user_id)
        ) & ((Idea.is_surprise == false()) | (Idea.created_by_user_id == requester_user_id))
    ideas = (
        await session.execute(
            select(Idea.id, Idea.title, Idea.status)
            .where(Idea.couple_id == couple_id, visible)
            .order_by(Idea.created_at.desc())
        )
    ).all()
    # A partner's vote stays hidden until both have voted (FR-VOTE-2): a "no" counts as passed
    # only for a requester who has voted on that idea too (their own "no" included).
    own_vote = aliased(Vote)
    voted_no: set[uuid.UUID] = set()
    if not shared:
        voted_no = set(
            (
                await session.execute(
                    select(Vote.idea_id)
                    .join(Idea, Idea.id == Vote.idea_id)
                    .where(
                        Idea.couple_id == couple_id,
                        Vote.value == "no",
                        exists().where(
                            own_vote.idea_id == Vote.idea_id,
                            own_vote.user_id == requester_user_id,
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )

    home: dict[str, Any] | None = None
    if couple.home_lat is not None and couple.home_lng is not None:
        home = {
            "label": couple.home_area_label,
            "lat": round(float(couple.home_lat), 2),
            "lng": round(float(couple.home_lng), 2),
        }

    context: dict[str, Any] = {
        "couple": {
            "timezone": couple.timezone,
            "today": today.isoformat(),
            "home": home,
            "constraints": _constraints(constraints),
        },
        "planner": _profile(planner) if planner is not None else None,
        "recipient": _profile(recipient),
        "occasions": _occasions(dates, today),
        "notes_about_recipient": [{"kind": kind, "text": text} for kind, text in notes],
        "taste": taste or None,
        "ideas": {
            "recent": [title for _, title, _ in ideas[:RECENT_IDEAS]],
            "active": [t for _, t, status in ideas if status == "active"][:ACTIVE_IDEAS],
            "passed": [
                t for i, t, status in ideas if status in ("archived", "expired") or i in voted_no
            ][:PASSED_IDEAS],
        },
    }
    return json.dumps(context, separators=(",", ":"), ensure_ascii=False)
