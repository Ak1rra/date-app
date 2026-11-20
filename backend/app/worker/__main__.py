"""Standalone worker for debugging: `python -m app.worker`. Exits cleanly on SIGTERM/SIGINT.

Normally the worker runs inside the API process. Running both against one database is safe:
claims use FOR UPDATE SKIP LOCKED, so a job is never handed to two workers at once.
"""

import asyncio
import logging
import signal
from types import FrameType

from app.config import get_settings
from app.db import make_engine, make_sessionmaker
from app.logging import setup_logging
from app.worker.runner import run_worker

logger = logging.getLogger("app.worker")


async def main() -> None:
    settings = get_settings()
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def request_stop(signum: int, _: FrameType | None) -> None:
        # signal.signal (not loop.add_signal_handler) so this also works on Windows dev boxes.
        logger.info("worker stopping", extra={"signal": signal.Signals(signum).name})
        loop.call_soon_threadsafe(stop.set)

    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        if sig := getattr(signal, name, None):
            signal.signal(sig, request_stop)

    engine = make_engine(settings)
    try:
        await run_worker(settings, stop, make_sessionmaker(engine))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    setup_logging(get_settings().log_level)
    asyncio.run(main())
