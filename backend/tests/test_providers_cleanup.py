"""The hourly cache.cleanup job (marker: db)."""

import datetime as dt
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Job, ProviderCache
from app.worker import handlers, runner
from app.worker.jobs import run_batch

pytestmark = pytest.mark.db

SessionMaker = async_sessionmaker[AsyncSession]
WORKER = "test-host:1"
FAR_FUTURE = dt.datetime(2099, 3, 4, 10, 30, tzinfo=dt.UTC)
HOUR = "cache.cleanup:2099-03-04T10"
NEXT_HOUR = "cache.cleanup:2099-03-04T11"


@pytest.fixture
async def sessionmaker(db_schema: str) -> AsyncIterator[SessionMaker]:
    engine = create_async_engine(db_schema)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def add_cache_rows(sm: SessionMaker) -> None:
    async with sm() as session:
        for key, offset in (
            ("expired", "-1 minute"),
            ("expired-long", "-3 days"),
            ("live", "+1 hour"),
        ):
            await session.execute(
                text(
                    "INSERT INTO provider_cache (provider, key, payload, expires_at) "
                    f"VALUES ('p', :key, '{{}}'::jsonb, now() + interval '{offset}')"
                ),
                {"key": key},
            )
        await session.commit()


async def job_by_key(sm: SessionMaker, key: str) -> Job | None:
    async with sm() as session:
        return (
            await session.execute(select(Job).where(Job.dedupe_key == key))
        ).scalar_one_or_none()


async def test_cache_cleanup_deletes_only_expired_rows_and_reschedules(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers, "_utcnow", lambda: FAR_FUTURE)
    await add_cache_rows(sessionmaker)
    async with sessionmaker() as session:
        assert await handlers.schedule_cache_cleanup(session, next_hour=False) is True
        await session.commit()
    assert await run_batch(sessionmaker, WORKER) == 1

    async with sessionmaker() as session:
        keys = set((await session.execute(select(ProviderCache.key))).scalars())
    assert keys == {"live"}
    done = await job_by_key(sessionmaker, HOUR)
    assert done is not None and done.status == "done"
    following = await job_by_key(sessionmaker, NEXT_HOUR)
    assert following is not None and following.status == "queued"
    assert following.run_at == dt.datetime(2099, 3, 4, 11, 0, tzinfo=dt.UTC)


async def test_scheduling_is_once_per_hour(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers, "_utcnow", lambda: FAR_FUTURE)
    async with sessionmaker() as session:
        assert await handlers.schedule_cache_cleanup(session, next_hour=False) is True
        assert await handlers.schedule_cache_cleanup(session, next_hour=False) is False
        assert await handlers.schedule_cache_cleanup(session, next_hour=True) is True
        assert await handlers.schedule_cache_cleanup(session, next_hour=True) is False
        await session.commit()
    assert handlers.CACHE_CLEANUP == "cache.cleanup"
    assert await job_by_key(sessionmaker, HOUR) is not None
    assert await job_by_key(sessionmaker, NEXT_HOUR) is not None


async def test_worker_startup_schedules_both_cleanups(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers, "_utcnow", lambda: FAR_FUTURE)
    await runner._startup(sessionmaker)
    assert await job_by_key(sessionmaker, "auth.cleanup:2099-03-04T10") is not None
    assert await job_by_key(sessionmaker, HOUR) is not None
