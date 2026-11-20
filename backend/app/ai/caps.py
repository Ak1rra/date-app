"""AI usage caps and accounting (COST-2/3).

`reserve` runs before an AI run: under a per-couple advisory lock it checks the caps, then
inserts a placeholder `ai_usage` row and commits. `finalize` fills that row in afterwards, whether
or not the run worked. Both use their OWN session from the app's sessionmaker and commit, so
they are independent of the request transaction: a failed run stays recorded even when the
request's own transaction rolls back.

How exact the caps are:
- The daily run counts are exact under concurrency. The placeholder counts the moment it is
  reserved, and the lock makes check-then-insert atomic per couple.
- The monthly token cap and the global dollar ceiling are SOFT. Tokens and cost are only known
  once a run ends, so runs already in flight (reserved, not yet finalized) are not in the sum, and
  the overshoot is bounded by how many can be in flight (the couple's daily caps; for the global
  ceiling, across all couples).
- A placeholder that is never finalized (the process died mid-run) stays as a failed run with
  zero tokens: still counted against the daily cap. That is conservative on purpose.
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.client import Usage
from app.config import Settings
from app.errors import ApiError
from app.models import AIUsage, Couple

Feature = Literal[
    "ideas",
    "plan_options",
    "plan_all",
    "build",
    "replan",
    "regenerate_stop",
    "chat",
    "hints",
    "polish",
    "taste",
]
Group = Literal["ideas", "builds", "chat"]

# Features that share a daily cap. Those not listed (hints, polish, taste) are uncapped per
# day, but still recorded, and still count towards the monthly token and global spend limits.
FEATURE_GROUPS: dict[Group, tuple[Feature, ...]] = {
    "ideas": ("ideas",),
    "builds": ("plan_options", "plan_all", "build", "replan", "regenerate_stop"),
    "chat": ("chat",),
}
_GROUP_LABELS: dict[Group, str] = {
    "ideas": "idea generations",
    "builds": "itinerary builds",
    "chat": "chat messages",
}

_PER_MTOK = Decimal(1_000_000)
_MICRO_USD = Decimal("0.000001")  # ai_usage.est_cost_usd is Numeric(12, 6)
_PENDING_MODEL = "pending"  # a reserved row until `finalize` fills in the real model

SessionMaker = async_sessionmaker[AsyncSession]


def _group_of(feature: Feature) -> Group | None:
    for group, features in FEATURE_GROUPS.items():
        if feature in features:
            return group
    return None


def daily_cap(settings: Settings, group: Group) -> int:
    return {
        "ideas": settings.ai_cap_ideas_per_day,
        "builds": settings.ai_cap_builds_per_day,
        "chat": settings.ai_cap_chat_per_day,
    }[group]


def _couple_tz(timezone: str | None) -> ZoneInfo | dt.tzinfo:
    try:
        return ZoneInfo(timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return dt.UTC


def local_day_bounds(timezone: str | None, now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """Start and end (the next local midnight) of the couple's current local day, both aware."""
    tz = _couple_tz(timezone)
    today = now.astimezone(tz).date()
    start = dt.datetime.combine(today, dt.time.min, tzinfo=tz)
    end = dt.datetime.combine(today + dt.timedelta(days=1), dt.time.min, tzinfo=tz)
    return start, end


def utc_month_bounds(now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    now = now.astimezone(dt.UTC)
    start = dt.datetime(now.year, now.month, 1, tzinfo=dt.UTC)
    end = (
        dt.datetime(now.year + 1, 1, 1, tzinfo=dt.UTC)
        if now.month == 12
        else dt.datetime(now.year, now.month + 1, 1, tzinfo=dt.UTC)
    )
    return start, end


def estimate_cost_usd(settings: Settings, usage: Usage) -> Decimal:
    """Usage x `ai_prices`, each line (attempt) at its own model's rates. A model with no entry
    is priced as the smart model (the dearer one, by default), so an unknown model is never
    under-counted."""
    prices = settings.ai_prices
    cost = Decimal(0)
    for model, *counts in usage.priced_lines():
        price = prices.get(model) or prices.get(settings.ai_model_smart)
        if price is None:  # misconfigured: use the dearest entry rather than nothing
            price = max(prices.values(), key=lambda p: p.get("output", 0.0), default={})
        cost += sum(
            Decimal(str(price.get(key, 0.0))) * count
            for key, count in zip(
                ("input", "output", "cache_read", "cache_write"), counts, strict=True
            )
        )
    return (Decimal(cost) / _PER_MTOK).quantize(_MICRO_USD)


async def _check(
    session: AsyncSession,
    settings: Settings,
    couple_id: uuid.UUID,
    feature: Feature,
    now: dt.datetime,
) -> None:
    """Raise `ApiError` if this run must not start:
    503 ai_paused when this month's estimated spend across all couples is at the global ceiling;
    429 ai_cap_reached (with `reset_at`) when the couple has used the day's runs of this
    feature's group (the couple's local day), or its tokens for the month (UTC)."""
    month_start, month_end = utc_month_bounds(now)

    spent = (
        await session.execute(
            select(func.coalesce(func.sum(AIUsage.est_cost_usd), 0)).where(
                AIUsage.created_at >= month_start, AIUsage.created_at < month_end
            )
        )
    ).scalar_one()
    if Decimal(spent) >= Decimal(str(settings.ai_global_monthly_usd)):
        raise ApiError(
            503, "ai_paused", "The planner is paused for the month. It will be back next month."
        )

    group = _group_of(feature)
    if group is not None:
        timezone = (
            await session.execute(select(Couple.timezone).where(Couple.id == couple_id))
        ).scalar_one_or_none()
        day_start, day_end = local_day_bounds(timezone, now)
        used = (
            await session.execute(
                select(func.count())
                .select_from(AIUsage)
                .where(
                    AIUsage.couple_id == couple_id,
                    AIUsage.feature.in_(FEATURE_GROUPS[group]),
                    AIUsage.created_at >= day_start,
                    AIUsage.created_at < day_end,
                )
            )
        ).scalar_one()
        cap = daily_cap(settings, group)
        if used >= cap:
            raise ApiError(
                429,
                "ai_cap_reached",
                f"You've used today's {cap} {_GROUP_LABELS[group]}. Try again tomorrow.",
                reset_at=day_end.isoformat(),
            )

    tokens = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(
                        AIUsage.input_tokens
                        + AIUsage.output_tokens
                        + AIUsage.cache_read_tokens
                        + AIUsage.cache_write_tokens
                    ),
                    0,
                )
            ).where(
                AIUsage.couple_id == couple_id,
                AIUsage.created_at >= month_start,
                AIUsage.created_at < month_end,
            )
        )
    ).scalar_one()
    if tokens >= settings.ai_monthly_tokens_per_couple:
        raise ApiError(
            429,
            "ai_cap_reached",
            "You've used this month's AI allowance. It resets at the start of next month.",
            reset_at=month_end.isoformat(),
        )


async def reserve(
    sessionmaker: SessionMaker,
    settings: Settings,
    *,
    couple_id: uuid.UUID,
    user_id: uuid.UUID | None,
    feature: Feature,
    now: dt.datetime | None = None,
) -> uuid.UUID:
    """Check the caps and, if the run may start, reserve a placeholder `ai_usage` row and return
    its id for `finalize`. Raises what the caps raise (see `_check`). Uncapped features skip the
    daily check but still reserve a row. Runs in its own committed transaction, serialised per
    couple by an advisory lock, so two concurrent runs can't both take the last daily slot."""
    now = now or dt.datetime.now(dt.UTC)
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"ai_caps:{couple_id}"},
        )
        await _check(session, settings, couple_id, feature, now)
        row = AIUsage(
            couple_id=couple_id,
            user_id=user_id,
            feature=feature,
            model=_PENDING_MODEL,
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            tool_calls=0,
            est_cost_usd=Decimal(0),
            ok=False,
            created_at=now,
        )
        session.add(row)
        await session.flush()
        return row.id


async def finalize(
    sessionmaker: SessionMaker,
    settings: Settings,
    reservation_id: uuid.UUID,
    *,
    usage: Usage,
    ok: bool,
    tool_calls: int,
) -> None:
    """Fill in the row `reserve` made with what the run used, failed runs included (they cost
    tokens too). Own session, committed, so it lands even if the request's transaction rolls
    back."""
    async with sessionmaker() as session, session.begin():
        await session.execute(
            update(AIUsage)
            .where(AIUsage.id == reservation_id)
            .values(
                model=usage.model,
                input_tokens=usage.input,
                output_tokens=usage.output,
                cache_read_tokens=usage.cache_read,
                cache_write_tokens=usage.cache_write,
                tool_calls=tool_calls,
                est_cost_usd=estimate_cost_usd(settings, usage),
                ok=ok,
            )
        )
