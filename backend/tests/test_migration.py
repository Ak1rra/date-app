"""The migrations against the real database (marker: db): they create what the models
declare, and downgrade/upgrade round-trips."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, Connection, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.models.base import Base

pytestmark = pytest.mark.db

EXPECTED_TABLES = {
    "users",
    "magic_link_tokens",
    "sessions",
    "couples",
    "couple_members",
    "partner_profiles",
    "availability_windows",
    "availability_blocks",
    "couple_constraints",
    "important_dates",
    "planner_notes",
    "jobs",
    # 0002
    "ideas",
    "votes",
    "invites",
    "profile_suggestions",
    "plan_sessions",
    "itineraries",
    "stops",
    "travel_legs",
    "checklist_items",
    "plan_templates",
    "surprise_hints",
    "ratings",
    "planner_reads",
    "taste_summaries",
    "chat_threads",
    "chat_messages",
    "notifications",
    "push_subscriptions",
    "provider_cache",
    "ai_usage",
}
V1_TABLES = EXPECTED_TABLES - {
    "users",
    "magic_link_tokens",
    "sessions",
    "couples",
    "couple_members",
    "partner_profiles",
    "availability_windows",
    "availability_blocks",
    "couple_constraints",
    "important_dates",
    "planner_notes",
    "jobs",
}


def _alembic(url: str, action: str, revision: str) -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    # A worker thread, as in conftest.db_schema: alembic's env.py calls asyncio.run.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(getattr(command, action), cfg, revision).result()


async def _inspect(url: str, fn):  # type: ignore[no-untyped-def]
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(fn)
    finally:
        await engine.dispose()


async def test_upgrade_creates_every_table(db_schema: str) -> None:
    def tables(conn: Connection) -> set[str]:
        return set(inspect(conn).get_table_names())

    assert await _inspect(db_schema, tables) == EXPECTED_TABLES | {"alembic_version"}
    assert {t.name for t in Base.metadata.sorted_tables} == EXPECTED_TABLES


async def test_migrated_schema_matches_the_models(db_schema: str) -> None:
    def diff(conn: Connection) -> list[object]:
        context = MigrationContext.configure(
            conn, opts={"compare_type": True, "compare_server_default": True}
        )
        return compare_metadata(context, Base.metadata)

    assert await _inspect(db_schema, diff) == []


async def test_check_constraints_and_partial_indexes_exist(db_schema: str) -> None:
    """compare_metadata ignores CHECK constraints and may not see index predicates, so look."""

    def check_names(conn: Connection) -> dict[str, set[str]]:
        insp = inspect(conn)
        return {t: {c["name"] for c in insp.get_check_constraints(t)} for t in EXPECTED_TABLES}

    expected = {
        table.name: {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
        for table in Base.metadata.sorted_tables
    }
    assert await _inspect(db_schema, check_names) == expected
    assert expected["availability_windows"] == {
        "ck_availability_windows_weekday_range",
        "ck_availability_windows_end_after_start",
    }

    def partial_indexes(conn: Connection) -> dict[str, str]:
        rows = conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'couple_members' AND indexname LIKE 'uq_%'"
            )
        )
        return {name: definition for name, definition in rows}

    indexes = await _inspect(db_schema, partial_indexes)
    assert set(indexes) == {"uq_couple_members_active_user", "uq_couple_members_active_role"}
    for definition in indexes.values():
        assert "UNIQUE" in definition
        assert "WHERE (left_at IS NULL)" in definition


async def test_server_defaults_exist(db_schema: str) -> None:
    def defaults(conn: Connection) -> dict[tuple[str, str], str | None]:
        rows = conn.execute(
            text(
                "SELECT table_name, column_name, column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_default IS NOT NULL"
            )
        )
        return {(t, c): d for t, c, d in rows}

    found = await _inspect(db_schema, defaults)
    assert "'UTC'" in (found[("users", "timezone")] or "")
    assert "'{}'::jsonb" in (found[("users", "notification_settings")] or "")
    assert "'[]'::jsonb" in (found[("partner_profiles", "liked_categories")] or "")
    assert "'USD'" in (found[("couple_constraints", "currency")] or "")
    assert found[("jobs", "attempts")] == "0"
    assert found[("jobs", "max_attempts")] == "5"
    assert "'queued'" in (found[("jobs", "status")] or "")
    assert ("couple_constraints", "id") not in found  # no id column: couple_id is the key


async def test_downgrade_then_upgrade_round_trips(db_schema: str) -> None:
    def tables(conn: Connection) -> set[str]:
        return set(inspect(conn).get_table_names())

    try:
        _alembic(db_schema, "downgrade", "base")
        assert await _inspect(db_schema, tables) <= {"alembic_version"}
    finally:
        _alembic(db_schema, "upgrade", "head")
    assert await _inspect(db_schema, tables) == EXPECTED_TABLES | {"alembic_version"}


async def test_downgrade_to_0001_drops_only_the_v1_tables(db_schema: str) -> None:
    def tables(conn: Connection) -> set[str]:
        return set(inspect(conn).get_table_names())

    try:
        _alembic(db_schema, "downgrade", "0001")
        assert await _inspect(db_schema, tables) == (
            (EXPECTED_TABLES - V1_TABLES) | {"alembic_version"}
        )
    finally:
        _alembic(db_schema, "upgrade", "head")
    assert await _inspect(db_schema, tables) == EXPECTED_TABLES | {"alembic_version"}


async def test_v1_constraints_that_reflection_hides(db_schema: str) -> None:
    """The stops position uniqueness is deferrable, the plan_sessions <-> itineraries cycle is
    closed by a named foreign key, and the chat thread indexes are partial and unique."""

    def look(conn: Connection) -> dict[str, tuple[bool, bool] | str]:
        found: dict[str, tuple[bool, bool] | str] = {}
        row = conn.execute(
            text(
                "SELECT condeferrable, condeferred FROM pg_constraint "
                "WHERE conname = 'uq_stops_itinerary_id'"
            )
        ).one()
        found["stops"] = (row[0], row[1])
        found["fk"] = conn.execute(
            text(
                "SELECT confdeltype::text FROM pg_constraint "
                "WHERE conname = 'fk_plan_sessions_itinerary_id_itineraries'"
            )
        ).scalar_one()
        for name, definition in conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'chat_threads' AND indexname LIKE 'uq_%'"
            )
        ):
            found[name] = definition
        return found

    found = await _inspect(db_schema, look)
    assert found["stops"] == (True, True)
    assert found["fk"] == "n"  # ON DELETE SET NULL
    for name, value in (
        ("uq_chat_threads_private_owner", "'private'"),
        ("uq_chat_threads_shared", "'shared'"),
    ):
        assert "UNIQUE" in str(found[name])
        assert "WHERE" in str(found[name])
        assert value in str(found[name])
