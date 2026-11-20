import asyncio
import logging
import time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import make_engine, make_sessionmaker
from app.main import create_app
from app.worker import runner
from tests.conftest import make_settings


def _unreachable_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(make_engine(make_settings()))  # DEAD_DB_URL: connections refused


async def test_run_worker_stops_when_event_set(settings: Settings) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run_worker(settings, stop, _unreachable_sessionmaker()))
    await asyncio.sleep(0.05)
    assert not task.done()
    stop.set()
    await asyncio.wait_for(task, timeout=1)


async def test_run_worker_survives_an_unreachable_database(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    stop = asyncio.Event()
    with caplog.at_level(logging.ERROR, logger="app.worker"):
        task = asyncio.create_task(runner.run_worker(settings, stop, _unreachable_sessionmaker()))
        await asyncio.sleep(0.3)
        assert not task.done()  # the failed startup and first tick did not end the loop
        stop.set()
        await asyncio.wait_for(task, timeout=1)
    messages = {r.getMessage() for r in caplog.records}
    assert {"worker could not schedule its periodic jobs", "worker tick failed"} <= messages


async def test_lifespan_starts_and_stops_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    async def fake_worker(settings: Settings, stop: asyncio.Event, sessionmaker: object) -> None:
        events.append("started")
        await stop.wait()
        events.append("stopped")

    monkeypatch.setattr("app.main.run_worker", fake_worker)
    app = create_app(make_settings(run_worker=True))
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.05)
        assert events == ["started"]
    assert events == ["started", "stopped"]


async def test_lifespan_without_worker_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake_worker(settings: Settings, stop: asyncio.Event, sessionmaker: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("app.main.run_worker", fake_worker)
    app = create_app(make_settings(run_worker=False))
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.05)
    assert not called


async def test_worker_that_raises_immediately_is_logged_and_shutdown_is_clean(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom(settings: Settings, stop: asyncio.Event, sessionmaker: object) -> None:
        raise RuntimeError("worker exploded")

    monkeypatch.setattr("app.main.run_worker", boom)
    app = create_app(make_settings(run_worker=True))
    with caplog.at_level(logging.ERROR, logger="app.main"):
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0.05)  # the task has already died; the API keeps serving
            # Logged now, while the API is still up, not only when shutdown inspects the task.
            failures = [r for r in caplog.records if r.message == "worker task failed"]
            assert len(failures) == 1
            assert failures[0].exc_info is not None
            assert "worker exploded" in str(failures[0].exc_info[1])
    # Shutdown must not log the same failure a second time.
    assert len([r for r in caplog.records if r.message == "worker task failed"]) == 1


async def test_worker_that_ignores_stop_and_cancellation_does_not_block_shutdown(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    release = asyncio.Event()
    cancels: list[int] = []

    async def stubborn(settings: Settings, stop: asyncio.Event, sessionmaker: object) -> None:
        while not release.is_set():
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                cancels.append(1)  # swallow it and carry on

    monkeypatch.setattr("app.main.run_worker", stubborn)
    monkeypatch.setattr("app.main.WORKER_STOP_TIMEOUT", 0.2)
    app = create_app(make_settings(run_worker=True))
    started = time.monotonic()
    with caplog.at_level(logging.ERROR, logger="app.main"):
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0.05)
    elapsed = time.monotonic() - started
    release.set()  # let the leftover task end so the test loop can close
    await asyncio.sleep(0.05)
    assert 0.2 <= elapsed < 2
    assert cancels
    assert any("did not stop" in r.message for r in caplog.records)
