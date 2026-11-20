"""The worker loop. Runs inside the API process (see main.create_app's lifespan) and, for
debugging, standalone via `python -m app.worker`.

Every TICK_SEC it claims and runs one batch of due jobs (app.worker.jobs). A batch is raced
against `stop`, so a slow or hung database call can never hold up shutdown for more than
STOP_GRACE_SEC.
"""

import asyncio
import contextlib
import logging
from collections.abc import Coroutine
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.worker import handlers
from app.worker.jobs import make_worker_id, run_batch

TICK_SEC = 2
STOP_GRACE_SEC = 3.0  # how long a batch in flight may keep going after `stop` is set
logger = logging.getLogger("app.worker")


async def _run_unless_stopped(work: Coroutine[Any, Any, Any], stop: asyncio.Event) -> None:
    """Await `work`, but once `stop` is set give it STOP_GRACE_SEC and then abandon it.

    An abandoned job stays `running` and is reclaimed after the stale-lock window, which the
    at-least-once contract already covers. Errors from `work` propagate.
    """
    task = asyncio.ensure_future(work)
    stop_waiter = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({task, stop_waiter}, return_when=asyncio.FIRST_COMPLETED)
        if not task.done():
            await asyncio.wait({task}, timeout=STOP_GRACE_SEC)
        if not task.done():
            logger.warning("worker batch abandoned at shutdown")
            task.cancel()
            # Never read again by anyone; retrieve its outcome so asyncio doesn't complain.
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            await asyncio.wait({task}, timeout=1.0)
            return
        task.result()
    finally:
        stop_waiter.cancel()
        if not task.done():
            task.cancel()  # we were cancelled ourselves


async def _startup(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """Make sure the current hour's auth.cleanup exists, so the hourly chain (re)starts."""
    async with sessionmaker() as session, session.begin():
        await handlers.schedule_auth_cleanup(session, next_hour=False)


async def run_worker(
    settings: Settings, stop: asyncio.Event, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Loop until `stop` is set. A failing tick (say, the database is down) is logged and
    retried on the next one; it never ends the loop."""
    worker_id = make_worker_id()
    logger.info("worker started", extra={"worker_id": worker_id})
    scheduled = False  # the hourly chain is (re)started once; retried until the database is up
    while not stop.is_set():
        if not scheduled:
            try:
                await _run_unless_stopped(_startup(sessionmaker), stop)
                scheduled = True
            except Exception:
                logger.exception("worker could not schedule its periodic jobs")
        try:
            await _run_unless_stopped(run_batch(sessionmaker, worker_id), stop)
        except Exception:
            logger.exception("worker tick failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=TICK_SEC)
    logger.info("worker stopped")
