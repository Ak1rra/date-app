"""The worker loop. Runs inside the API process (see main.create_app's lifespan) and, for
debugging, standalone via `python -m app.worker`.

Stub for now: ticks every 2 s. The job runner (claim, run, retry) is a later task and
replaces the tick body.
"""

import asyncio
import contextlib
import logging

from app.config import Settings

TICK_SEC = 2
logger = logging.getLogger("app.worker")


async def run_worker(settings: Settings, stop: asyncio.Event) -> None:
    """Loop until `stop` is set."""
    logger.info("worker started")
    while not stop.is_set():
        # tick: the job runner goes here
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=TICK_SEC)
    logger.info("worker stopped")
