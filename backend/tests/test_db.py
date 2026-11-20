"""Tests that need the real Postgres at TEST_DATABASE_URL (marker: db)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.db


async def test_health_ok_against_real_db(db_client: AsyncClient) -> None:
    response = await db_client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


async def test_alembic_upgrade_head_ran_on_empty_schema(db_session: AsyncSession) -> None:
    # db_schema dropped/recreated `public` and ran `alembic upgrade head` without error.
    # With no models yet, the only table that can exist is Alembic's own version table.
    tables = await db_session.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    )
    assert {row[0] for row in tables} <= {"alembic_version"}
