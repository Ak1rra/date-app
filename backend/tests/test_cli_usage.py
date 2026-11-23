"""`dateapp usage`: the per-couple, per-feature monthly AI usage report and its formatting.

`_usage_report` runs against the test database; the command's output is checked with the report
and settings replaced on the cli module, so those tests need no database.
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
from click.testing import CliRunner
from sqlalchemy.ext.asyncio import AsyncSession

from app import cli as cli_mod
from app.models import AIUsage, Couple, CoupleMember, User
from tests.conftest import make_settings

NOV = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
DEC = dt.datetime(2026, 12, 1, tzinfo=dt.UTC)


def invoke(*args: str) -> Any:
    return CliRunner().invoke(cli_mod.cli, list(args))


def add_usage(
    session: AsyncSession,
    couple_id: uuid.UUID | None,
    feature: str,
    at: dt.datetime,
    *,
    tokens_in: int,
    tokens_out: int,
    cost: str,
) -> None:
    session.add(
        AIUsage(
            couple_id=couple_id,
            feature=feature,
            model="gpt-6.2-sol",
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cache_read_tokens=0,
            cache_write_tokens=0,
            tool_calls=0,
            est_cost_usd=Decimal(cost),
            ok=True,
            created_at=at,
        )
    )


@pytest.mark.db
async def test_usage_report_sums_per_couple_and_feature_for_the_month_only(
    db_session: AsyncSession,
) -> None:
    alex = User(email="alex@example.com", display_name="Alex")
    sam = User(email="sam@example.com", display_name="Sam")
    couple_a, couple_b = Couple(), Couple()
    db_session.add_all([alex, sam, couple_a, couple_b])
    await db_session.flush()
    db_session.add_all(
        [
            CoupleMember(couple_id=couple_a.id, user_id=alex.id, role="owner"),
            CoupleMember(couple_id=couple_a.id, user_id=sam.id, role="partner"),
        ]
    )
    mid = NOV + dt.timedelta(days=10)
    add_usage(db_session, couple_a.id, "ideas", mid, tokens_in=100, tokens_out=10, cost="0.10")
    add_usage(db_session, couple_a.id, "ideas", mid, tokens_in=200, tokens_out=20, cost="0.20")
    add_usage(db_session, couple_a.id, "chat", mid, tokens_in=50, tokens_out=5, cost="0.05")
    add_usage(db_session, couple_b.id, "ideas", NOV, tokens_in=7, tokens_out=3, cost="0.01")
    add_usage(db_session, None, "chat", mid, tokens_in=1, tokens_out=1, cost="0.02")
    # Outside the month: the last instant of October and the first of December.
    before = NOV - dt.timedelta(microseconds=1)
    add_usage(db_session, couple_a.id, "ideas", before, tokens_in=999, tokens_out=999, cost="9")
    add_usage(db_session, couple_a.id, "ideas", DEC, tokens_in=999, tokens_out=999, cost="9")
    await db_session.commit()

    report = await cli_mod._usage_report(db_session, NOV, DEC)

    assert report.total_cost_usd == Decimal("0.38")
    by_id = {c.couple_id: c for c in report.couples}
    a = by_id[couple_a.id]
    assert a.label == f"Couple {couple_a.id.hex[:8]} - Alex & Sam"
    ideas = next(f for f in a.features if f.feature == "ideas")
    assert (ideas.runs, ideas.input_tokens, ideas.output_tokens) == (2, 300, 30)
    assert ideas.cost_usd == Decimal("0.30")
    chat = next(f for f in a.features if f.feature == "chat")
    assert (chat.runs, chat.cost_usd) == (1, Decimal("0.05"))
    assert (a.runs, a.input_tokens, a.output_tokens) == (3, 350, 35)
    assert a.cost_usd == Decimal("0.35")
    b = by_id[couple_b.id]
    assert b.label == f"Couple {couple_b.id.hex[:8]}"  # no members: just the short id
    assert b.runs == 1
    orphan = by_id[None]
    assert orphan.label == "(no couple)"
    assert orphan.cost_usd == Decimal("0.02")
    costs = [c.cost_usd for c in report.couples]
    assert costs == sorted(costs, reverse=True)


@pytest.mark.db
async def test_usage_report_is_empty_for_a_month_with_no_rows(db_session: AsyncSession) -> None:
    report = await cli_mod._usage_report(db_session, NOV, DEC)
    assert report.couples == []
    assert report.total_cost_usd == Decimal(0)


def patch_cli(
    monkeypatch: pytest.MonkeyPatch, report: cli_mod.UsageReport, ceiling: float = 25.0
) -> list[tuple[dt.datetime, dt.datetime]]:
    calls: list[tuple[dt.datetime, dt.datetime]] = []

    async def fake_load(_settings: Any, start: dt.datetime, end: dt.datetime) -> Any:
        calls.append((start, end))
        return report

    monkeypatch.setattr(cli_mod, "_settings", lambda: make_settings(ai_global_monthly_usd=ceiling))
    monkeypatch.setattr(cli_mod, "_load_usage", fake_load)
    return calls


def test_usage_prints_couples_features_and_the_global_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    couple_id = uuid.UUID("1a2b3c4d-0000-0000-0000-000000000000")
    report = cli_mod.UsageReport(
        [
            cli_mod.CoupleUsage(
                couple_id,
                "Couple 1a2b3c4d - Alex & Sam",
                [
                    cli_mod.FeatureUsage("ideas", 12, 34120, 8210, Decimal("0.41")),
                    cli_mod.FeatureUsage("chat", 5, 1000, 200, Decimal("0.14")),
                ],
            )
        ]
    )
    calls = patch_cli(monkeypatch, report)

    result = invoke("usage", "--month", "2026-11")

    assert result.exit_code == 0, result.output
    assert calls == [(NOV, DEC)]
    out = result.output
    assert "usage" in out
    assert "2026-11 (UTC)" in out
    assert "Couple 1a2b3c4d - Alex & Sam" in out
    assert "12 runs" in out
    assert "34,120 in" in out
    assert "8,210 out" in out
    assert "$0.41" in out
    total_line = next(line for line in out.splitlines() if line.strip().startswith("total"))
    assert "17 runs" in total_line
    assert "$0.55" in total_line
    assert "Global: $0.55 of $25.00 ceiling (2.2%)" in out


def test_usage_with_no_rows_says_so_and_still_prints_the_global_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_cli(monkeypatch, cli_mod.UsageReport([]))

    result = invoke("usage", "--month", "2026-11")

    assert result.exit_code == 0, result.output
    assert "No AI usage in 2026-11." in result.output
    assert "Global: $0.00 of $25.00 ceiling (0.0%)" in result.output


def test_usage_defaults_to_the_current_utc_month(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = patch_cli(monkeypatch, cli_mod.UsageReport([]))

    assert invoke("usage").exit_code == 0

    start, end = calls[0]
    assert start <= dt.datetime.now(dt.UTC) < end
    assert (start.day, start.hour) == (1, 0)


def test_usage_handles_a_zero_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_cli(monkeypatch, cli_mod.UsageReport([]), ceiling=0)

    result = invoke("usage", "--month", "2026-11")

    assert result.exit_code == 0, result.output
    assert "ceiling (n/a)" in result.output


@pytest.mark.parametrize(
    "value",
    ["2026-13", "2026-00", "2026-1", "26-11", "nov", "2026-11-01", "0000-01", "9999-12", ""],
)
def test_usage_rejects_an_invalid_month(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    calls = patch_cli(monkeypatch, cli_mod.UsageReport([]))

    result = invoke("usage", "--month", value)

    assert result.exit_code == 1
    assert "YYYY-MM" in result.output
    assert calls == []


def test_usage_fails_cleanly_when_the_database_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*_args: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(cli_mod, "_settings", lambda: make_settings())
    monkeypatch.setattr(cli_mod, "_load_usage", boom)

    result = invoke("usage")

    assert result.exit_code == 1
    assert "database up and migrated" in result.output


def test_help_lists_usage() -> None:
    result = invoke("--help")
    assert result.exit_code == 0
    assert "usage" in result.output
