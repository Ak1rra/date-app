import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import router as v1_router
from app.config import Settings, get_settings
from app.db import make_engine, make_sessionmaker
from app.errors import install_error_handlers
from app.frontend import FrontendMiddleware, check_frontend
from app.logging import RequestContextMiddleware, setup_logging
from app.security.csrf import CSRF_HEADER_NAME, CSRFMiddleware
from app.security.headers import SecurityHeadersMiddleware
from app.worker.runner import run_worker

logger = logging.getLogger(__name__)


WORKER_STOP_TIMEOUT = 10.0  # seconds; read at shutdown, so tests can shorten it


async def _stop_worker(task: asyncio.Task[None]) -> None:
    """Wait for a worker that was told to stop. Nothing it does can escape the lifespan."""
    done, _ = await asyncio.wait({task}, timeout=WORKER_STOP_TIMEOUT)
    if not done:
        task.cancel()  # not awaited again: a worker that ignores cancellation can't block exit
        logger.error("worker did not stop in %s s; cancelled it", WORKER_STOP_TIMEOUT)
    # A failure was already logged by _log_worker_failure when the task finished.


def _log_worker_failure(task: asyncio.Task[None]) -> None:
    """Done-callback: report a worker that dies while the API keeps serving."""
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.error("worker task failed", exc_info=error)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    if settings.sentry_dsn:
        sentry_sdk.init(dsn=settings.sentry_dsn, send_default_pii=False)

    engine = make_engine(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("api started", extra={"demo_mode": settings.demo_mode})
        check_frontend(settings)
        stop = asyncio.Event()
        worker_task: asyncio.Task[None] | None = None
        if settings.run_worker:
            worker_task = asyncio.create_task(run_worker(settings, stop), name="worker")
            worker_task.add_done_callback(_log_worker_failure)
        try:
            yield
        finally:
            stop.set()
            if worker_task is not None:
                await _stop_worker(worker_task)
            await engine.dispose()

    app = FastAPI(
        title="Date Planner API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)

    install_error_handlers(app)
    app.include_router(v1_router, prefix="/api/v1")

    # add_middleware puts the last one added outermost, so read this bottom-up:
    # request id + access log > security headers > CORS > CSRF > frontend files > app.
    app.add_middleware(FrontendMiddleware, settings=settings)
    app.add_middleware(CSRFMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", CSRF_HEADER_NAME],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app


app = create_app()
