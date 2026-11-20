"""AI caps and accounting: daily (couple-local day), monthly tokens, global spend, reserve."""

import asyncio
import datetime as dt
import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai import caps
from app.ai.client import Usage
from app.config import Settings
from app.errors import ApiError, install_error_handlers
from app.models import AIUsage, Couple
from tests.conftest import make_settings

db = pytest.mark.db

# 23:00 on 14 June in New York, so the couple's local day runs 14 June 04:00 UTC to 15 June 04:00
# UTC while the UTC date is already the 15th.
NOW = dt.datetime(2030, 6, 15, 3, 0, tzinfo=dt.UTC)
DAY_START = dt.datetime(2030, 6, 14, 4, 0, tzinfo=dt.UTC)
NEW_YORK = "America/New_York"


async def make_couple(session: AsyncSession, timezone: str = NEW_YORK) -> uuid.UUID:
    couple = Couple(timezone=timezone)
    session.add(couple)
    await session.commit()
    return couple.id


async def add_usage(
    session: AsyncSession,
    couple_id: uuid.UUID | None,
    feature: str,
    at: dt.datetime,
    *,
    ok: bool = True,
    tokens: int = 10,
    cost: str = "0",
) -> None:
    session.add(
        AIUsage(
            couple_id=couple_id,
            feature=feature,
            model="claude-sonnet-5-5",
            input_tokens=tokens,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            tool_calls=0,
            est_cost_usd=Decimal(cost),
            ok=ok,
            created_at=at,
        )
    )
    await session.commit()


@pytest.fixture
def sessionmaker(db_session: AsyncSession) -> caps.SessionMaker:
    """A sessionmaker on the test engine, as the app's `app.state.sessionmaker` is."""
    assert db_session.bind is not None
    return async_sessionmaker(db_session.bind, expire_on_commit=False)


async def reserve(
    sessionmaker: caps.SessionMaker,
    settings: Settings,
    couple_id: uuid.UUID,
    feature: str,
    now: dt.datetime = NOW,
) -> uuid.UUID:
    return await caps.reserve(
        sessionmaker,
        settings,
        couple_id=couple_id,
        user_id=None,
        feature=feature,  # type: ignore[arg-type]
        now=now,
    )


def cfg(**overrides: object) -> Settings:
    return make_settings(**overrides)


# --- Pure helpers ---------------------------------------------------------------------------


def test_local_day_bounds_follow_the_couple_timezone() -> None:
    start, end = caps.local_day_bounds(NEW_YORK, NOW)
    assert start.astimezone(dt.UTC) == DAY_START
    assert end.isoformat() == "2030-06-15T00:00:00-04:00"
    # An unknown timezone falls back to UTC rather than failing the run.
    start, end = caps.local_day_bounds("Mars/Olympus", NOW)
    assert start == dt.datetime(2030, 6, 15, tzinfo=dt.UTC)


def test_utc_month_bounds_roll_over_the_year() -> None:
    assert caps.utc_month_bounds(NOW) == (
        dt.datetime(2030, 6, 1, tzinfo=dt.UTC),
        dt.datetime(2030, 7, 1, tzinfo=dt.UTC),
    )
    _, end = caps.utc_month_bounds(dt.datetime(2030, 12, 31, 23, tzinfo=dt.UTC))
    assert end == dt.datetime(2031, 1, 1, tzinfo=dt.UTC)


def test_estimate_cost_uses_the_model_prices_and_prices_unknown_models_as_smart() -> None:
    s = cfg()
    sonnet = Usage("claude-sonnet-5-5", input=1_000_000, output=1_000_000, cache_read=1_000_000)
    assert caps.estimate_cost_usd(s, sonnet) == Decimal("12.100000")  # 2.00 + 10.00 + 0.10
    haiku = Usage("claude-haiku-5-5", input=1_000_000, output=1_000_000, cache_write=1_000_000)
    assert caps.estimate_cost_usd(s, haiku) == Decimal("0.725000")  # 0.10 + 0.50 + 0.125
    unknown = Usage("claude-mystery-9", input=1_000_000, output=1_000_000)
    assert caps.estimate_cost_usd(s, unknown) == Decimal("12.000000")
    assert caps.estimate_cost_usd(s, Usage("fake")) == Decimal("0")


def price(input: float, output: float) -> dict[str, float]:
    return {"input": input, "output": output, "cache_read": 0.0, "cache_write": 0.0}


def test_estimate_cost_prices_each_line_at_its_own_models_rates() -> None:
    prices = {
        "claude-haiku-5-5": price(0.10, 0.50),
        "claude-sonnet-5-5": price(2.00, 10.00),
        "claude-sonnet-5": price(3.00, 20.00),
    }
    s = cfg(ai_prices=prices)
    usage = Usage(
        "claude-sonnet-5",
        input=2000,
        output=500,
        lines=(
            ("claude-sonnet-5-5", 1000, 0, 0, 0),  # the attempt that fell back
            ("claude-sonnet-5", 1000, 500, 0, 0),
        ),
    )
    # 1000 x 2.00 + 1000 x 3.00 + 500 x 20.00 = 15000 per million. Priced all at one model it
    # would be 0.009 (sonnet-5-5) or 0.016 (sonnet-5).
    assert caps.estimate_cost_usd(s, usage) == Decimal("0.015000")
    # A line whose model has no entry is priced as the smart model.
    unknown = Usage("x", input=1000, lines=(("claude-mystery-9", 1000, 0, 0, 0),))
    assert caps.estimate_cost_usd(s, unknown) == Decimal("0.002000")


def test_estimate_cost_without_lines_prices_the_totals_at_the_model() -> None:
    prices = {"claude-haiku-5-5": price(0.10, 0.50), "claude-sonnet-5-5": price(2.00, 10.00)}
    s = cfg(ai_prices=prices)
    assert caps.estimate_cost_usd(s, Usage("claude-sonnet-5-5", input=1000, output=500)) == Decimal(
        "0.007000"
    )
    assert caps.estimate_cost_usd(s, Usage("claude-sonnet-5-5")) == Decimal("0.000000")


def test_the_default_prices_pass_validation() -> None:
    assert cfg().ai_prices


def test_the_env_example_ai_prices_line_is_accepted_by_settings() -> None:
    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    if not env_example.exists():  # scripts/remote-check.sh syncs only backend/
        pytest.skip(".env.example is outside the synced tree")
    [line] = [x for x in env_example.read_text().splitlines() if x.startswith("# AI_PRICES=")]
    prices = json.loads(line.removeprefix("# AI_PRICES="))
    settings = cfg(ai_prices=prices)  # the default model names, so both need an entry
    assert {settings.ai_model_smart, settings.ai_model_fast} <= set(settings.ai_prices)


@pytest.mark.parametrize(
    ("prices", "message"),
    [
        ({}, "no entry"),
        (
            {"claude-haiku-5-5": price(0.1, 0.5), "claude-sonnet-5-5": {"input": 1.0}},
            "exactly the keys",
        ),
        (
            {"claude-haiku-5-5": price(0.1, -0.5), "claude-sonnet-5-5": price(2.0, 10.0)},
            ">= 0",
        ),
        (
            {"claude-haiku-5-5": price(0.1, float("nan")), "claude-sonnet-5-5": price(2.0, 1.0)},
            "finite",
        ),
        ({"claude-haiku-5-5": price(0.1, 0.5)}, "claude-sonnet-5-5"),  # no smart-model entry
    ],
)
def test_bad_price_maps_fail_settings_validation(
    prices: dict[str, dict[str, float]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        cfg(ai_prices=prices)


async def test_api_error_extra_fields_reach_the_error_body() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/capped")
    async def capped() -> None:
        raise ApiError(429, "ai_cap_reached", "Nope.", reset_at="2030-06-15T00:00:00-04:00")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get("/capped")
    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": "ai_cap_reached",
            "message": "Nope.",
            "reset_at": "2030-06-15T00:00:00-04:00",
        }
    }


# --- reserve ----------------------------------------------------------------------------------


@db
async def test_daily_cap_counts_the_couples_local_day_and_gives_the_next_midnight(
    db_session: AsyncSession, sessionmaker: caps.SessionMaker
) -> None:
    couple_id = await make_couple(db_session)
    s = cfg(ai_cap_ideas_per_day=2)
    # One run just before the local day (not counted) and one inside it.
    await add_usage(db_session, couple_id, "ideas", DAY_START - dt.timedelta(minutes=1))
    await add_usage(db_session, couple_id, "ideas", DAY_START + dt.timedelta(minutes=1))
    await reserve(sessionmaker, s, couple_id, "ideas")  # 1 of 2; the reservation now counts

    with pytest.raises(ApiError) as caught:
        await reserve(sessionmaker, s, couple_id, "ideas")
    err = caught.value
    assert (err.status, err.code) == (429, "ai_cap_reached")
    assert err.extra == {"reset_at": "2030-06-15T00:00:00-04:00"}

    # The next local day starts afresh.
    await reserve(sessionmaker, s, couple_id, "ideas", NOW + dt.timedelta(hours=2))


@db
async def test_a_failed_run_counts_towards_the_daily_cap(
    db_session: AsyncSession, sessionmaker: caps.SessionMaker
) -> None:
    couple_id = await make_couple(db_session)
    s = cfg(ai_cap_ideas_per_day=1)
    await add_usage(db_session, couple_id, "ideas", NOW - dt.timedelta(hours=1), ok=False)
    with pytest.raises(ApiError) as caught:
        await reserve(sessionmaker, s, couple_id, "ideas")
    assert caught.value.code == "ai_cap_reached"


@db
async def test_feature_groups_share_a_cap_and_other_groups_and_couples_do_not(
    db_session: AsyncSession, sessionmaker: caps.SessionMaker
) -> None:
    couple_id = await make_couple(db_session)
    other_id = await make_couple(db_session)
    s = cfg(ai_cap_builds_per_day=3, ai_cap_ideas_per_day=1, ai_cap_chat_per_day=1)
    at = NOW - dt.timedelta(hours=1)
    for feature in ("plan_options", "build", "regenerate_stop"):
        await add_usage(db_session, couple_id, feature, at)
    await add_usage(db_session, other_id, "ideas", at)  # someone else's run

    for feature in ("plan_options", "plan_all", "build", "replan", "regenerate_stop"):
        with pytest.raises(ApiError) as caught:
            await reserve(sessionmaker, s, couple_id, feature)
        assert caught.value.code == "ai_cap_reached"
    # Ideas and chat are separate groups with their own (unused) counts.
    await reserve(sessionmaker, s, couple_id, "ideas")
    await reserve(sessionmaker, s, couple_id, "chat")


@db
async def test_uncapped_features_reserve_however_often_they_ran(
    db_session: AsyncSession, sessionmaker: caps.SessionMaker
) -> None:
    couple_id = await make_couple(db_session)
    s = cfg(ai_cap_ideas_per_day=1, ai_cap_builds_per_day=1, ai_cap_chat_per_day=1)
    for feature in ("hints", "polish", "taste") * 3:
        await add_usage(db_session, couple_id, feature, NOW - dt.timedelta(hours=1))
    for feature in ("hints", "polish", "taste") * 2:
        await reserve(sessionmaker, s, couple_id, feature)
    rows = (await db_session.execute(select(AIUsage).where(AIUsage.model == "pending"))).all()
    assert len(rows) == 6  # each still reserved a row


@db
async def test_monthly_token_cap_is_per_couple_and_utc_month(
    db_session: AsyncSession, sessionmaker: caps.SessionMaker
) -> None:
    couple_id = await make_couple(db_session)
    other_id = await make_couple(db_session)
    s = cfg(ai_monthly_tokens_per_couple=1000)
    await add_usage(
        db_session, couple_id, "hints", dt.datetime(2030, 5, 31, 23, tzinfo=dt.UTC), tokens=5000
    )
    await add_usage(
        db_session, other_id, "hints", dt.datetime(2030, 6, 2, tzinfo=dt.UTC), tokens=5000
    )
    await add_usage(
        db_session, couple_id, "hints", dt.datetime(2030, 6, 2, tzinfo=dt.UTC), tokens=999
    )
    await reserve(sessionmaker, s, couple_id, "hints")  # under the cap

    await add_usage(
        db_session, couple_id, "chat", dt.datetime(2030, 6, 10, tzinfo=dt.UTC), tokens=1
    )
    with pytest.raises(ApiError) as caught:
        await reserve(sessionmaker, s, couple_id, "hints")
    err = caught.value
    assert (err.status, err.code) == (429, "ai_cap_reached")
    assert err.extra == {"reset_at": "2030-07-01T00:00:00+00:00"}
    await reserve(sessionmaker, s, couple_id, "hints", NOW + dt.timedelta(days=17))


@db
async def test_global_spend_ceiling_pauses_everyone(
    db_session: AsyncSession, sessionmaker: caps.SessionMaker
) -> None:
    couple_id = await make_couple(db_session)
    other_id = await make_couple(db_session)
    s = cfg(ai_global_monthly_usd=10.0)
    await add_usage(
        db_session, other_id, "chat", dt.datetime(2030, 5, 30, tzinfo=dt.UTC), cost="50"
    )
    await add_usage(
        db_session, other_id, "chat", dt.datetime(2030, 6, 3, tzinfo=dt.UTC), cost="9.999999"
    )
    await reserve(sessionmaker, s, couple_id, "hints")

    await add_usage(
        db_session, None, "chat", dt.datetime(2030, 6, 4, tzinfo=dt.UTC), cost="0.000001"
    )
    for feature in ("hints", "ideas"):
        with pytest.raises(ApiError) as caught:
            await reserve(sessionmaker, s, couple_id, feature)
        assert (caught.value.status, caught.value.code) == (503, "ai_paused")


@db
async def test_monthly_sums_ignore_rows_dated_after_the_month(
    db_session: AsyncSession, sessionmaker: caps.SessionMaker
) -> None:
    couple_id = await make_couple(db_session)
    s = cfg(ai_monthly_tokens_per_couple=1000, ai_global_monthly_usd=10.0)
    next_month = dt.datetime(2030, 7, 2, tzinfo=dt.UTC)
    await add_usage(db_session, couple_id, "hints", next_month, tokens=5000, cost="50")
    await reserve(sessionmaker, s, couple_id, "hints")  # June: neither the tokens nor the cost
    with pytest.raises(ApiError):  # but July counts them
        await reserve(sessionmaker, s, couple_id, "hints", next_month + dt.timedelta(days=1))


@db
async def test_concurrent_reserves_cannot_both_take_the_last_slot(
    db_session: AsyncSession, sessionmaker: caps.SessionMaker
) -> None:
    couple_id = await make_couple(db_session)
    s = cfg(ai_cap_ideas_per_day=2)
    await add_usage(db_session, couple_id, "ideas", NOW - dt.timedelta(hours=1))  # one slot left

    results = await asyncio.gather(
        *(reserve(sessionmaker, s, couple_id, "ideas") for _ in range(2)),
        return_exceptions=True,
    )
    ok = [r for r in results if isinstance(r, uuid.UUID)]
    failed = [r for r in results if isinstance(r, ApiError)]
    assert len(ok) == 1 and len(failed) == 1
    assert failed[0].code == "ai_cap_reached"
    rows = (await db_session.execute(select(AIUsage).where(AIUsage.feature == "ideas"))).all()
    assert len(rows) == 2


# --- finalize ---------------------------------------------------------------------------------


@db
async def test_reserve_then_finalize_fills_the_row_for_ok_and_failed_runs(
    db_session: AsyncSession, sessionmaker: caps.SessionMaker
) -> None:
    couple_id = await make_couple(db_session)
    s = cfg()
    build_id = await reserve(sessionmaker, s, couple_id, "build")
    ideas_id = await reserve(sessionmaker, s, couple_id, "ideas")

    # Until finalized a reservation is a failed, empty "pending" run.
    pending = (await db_session.execute(select(AIUsage).where(AIUsage.id == build_id))).scalar_one()
    assert (pending.model, pending.ok, pending.input_tokens) == ("pending", False, 0)

    usage = Usage("claude-sonnet-5-5", input=500_000, output=100_000, cache_read=2_000_000)
    await caps.finalize(sessionmaker, s, build_id, usage=usage, ok=True, tool_calls=4)
    await caps.finalize(
        sessionmaker, s, ideas_id, usage=Usage("claude-haiku-5-5", input=10), ok=False, tool_calls=0
    )

    db_session.expire_all()
    rows = (await db_session.execute(select(AIUsage).order_by(AIUsage.feature))).scalars().all()
    build, ideas = rows
    assert (build.feature, build.ok, build.tool_calls, build.model) == (
        "build",
        True,
        4,
        "claude-sonnet-5-5",
    )
    assert (build.input_tokens, build.output_tokens, build.cache_read_tokens) == (
        500_000,
        100_000,
        2_000_000,
    )
    assert build.est_cost_usd == Decimal("2.200000")  # 1.00 + 1.00 + 0.20
    assert (ideas.feature, ideas.ok, ideas.input_tokens) == ("ideas", False, 10)

    # What finalize wrote is what the next reserve counts.
    with pytest.raises(ApiError):
        await reserve(sessionmaker, cfg(ai_cap_builds_per_day=1), couple_id, "build")


@db
async def test_a_failed_run_stays_recorded_when_the_callers_transaction_rolls_back(
    db_session: AsyncSession, sessionmaker: caps.SessionMaker
) -> None:
    couple_id = await make_couple(db_session)
    s = cfg()
    usage = Usage("claude-haiku-5-5", input=7)
    async with sessionmaker() as request_session:
        await request_session.execute(select(Couple.id))  # the request's transaction is open
        reservation = await reserve(sessionmaker, s, couple_id, "chat")
        await caps.finalize(sessionmaker, s, reservation, usage=usage, ok=False, tool_calls=1)
        await request_session.rollback()

    row = (await db_session.execute(select(AIUsage))).scalar_one()
    assert (row.feature, row.ok, row.input_tokens, row.model) == (
        "chat",
        False,
        7,
        "claude-haiku-5-5",
    )
