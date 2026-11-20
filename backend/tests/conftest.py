"""Shared fixtures.

Everything here works without a database except the `db`-marked fixtures at the bottom,
which fail loudly (never skip) when TEST_DATABASE_URL is unreachable.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi import HTTPException as FastAPIHTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import Settings
from app.deps import current_user
from app.errors import ApiError
from app.main import create_app
from app.models.base import Base
from app.providers.email.console import OUTBOX

ORIGIN = "http://localhost:8080"
NO_DIST = Path(__file__).parent / "_no_dist"
# A port nothing listens on, so DB access fails fast.
DEAD_DB_URL = "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/nothing"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "demo_mode": True,
        "cookie_secure": False,
        "app_origin": ORIGIN,
        "database_url": DEAD_DB_URL,
        "sentry_dsn": None,
        "run_worker": False,
        # Never the real frontend/dist, whatever has been built in this checkout.
        "frontend_dist": NO_DIST,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class EchoIn(BaseModel):
    name: str
    count: int


def _test_router() -> APIRouter:
    """Routes that exist only in tests, to exercise middleware on every method/path shape."""
    router = APIRouter()

    @router.api_route("/_test/echo", methods=["POST", "PUT", "PATCH", "DELETE"])
    async def echo() -> dict[str, bool]:
        return {"ok": True}

    @router.post("/_test/validate")
    async def validate(body: EchoIn) -> EchoIn:
        return body

    @router.get("/_test/limited")
    async def limited() -> None:
        raise ApiError(429, "rate_limited", "Slow down.", {"Retry-After": "7"})

    @router.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    @router.get("/_test/teapot")
    async def teapot() -> None:
        raise FastAPIHTTPException(status_code=418, detail="x")

    @router.get("/_test/private")
    async def private(_: None = Depends(current_user)) -> None:
        return None

    @router.get("/me")
    async def me() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/auth/probe")
    async def auth_probe() -> dict[str, bool]:
        return {"ok": True}

    return router


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    application = create_app(settings)
    application.include_router(_test_router(), prefix="/api/v1")
    return application


@pytest.fixture(autouse=True)
def _clean_outbox() -> Iterator[None]:
    OUTBOX.clear()
    yield
    OUTBOX.clear()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def csrf_client(client: AsyncClient) -> AsyncClient:
    """A client that already holds a CSRF cookie and sends the matching header."""
    await client.get("/api/v1/meta")
    client.headers["X-CSRF-Token"] = client.cookies["csrf_token"]
    return client


# --- Database fixtures (marker: db). Provided for later tasks; not used by the DB-free suite.


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            # Last line of defence: only ever wipe a scratch test database.
            name = (await conn.execute(text("SELECT current_database()"))).scalar_one()
            if not str(name).startswith("dateapp_test"):
                raise RuntimeError(f"refusing to reset schema of non-test database {name!r}")
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return make_settings().test_database_url


@pytest.fixture(scope="session")
def db_schema(test_database_url: str) -> str:
    """Drop and recreate the public schema, then `alembic upgrade head`."""
    from alembic import command
    from alembic.config import Config

    # Worker thread: asyncio.run (here and in alembic's env.py) is illegal on a thread whose
    # event loop is running, and this fixture can be requested from an async fixture.
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            pool.submit(asyncio.run, _reset_schema(test_database_url)).result()
        except Exception as exc:
            pytest.fail(
                f"TEST_DATABASE_URL is unreachable ({type(exc).__name__}: {exc}). "
                "Start Postgres and create the test database; db tests never skip.",
                pytrace=False,
            )
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", test_database_url.replace("%", "%%"))
        pool.submit(command.upgrade, cfg, "head").result()
    return test_database_url


@pytest.fixture(autouse=True)
async def _truncate_tables(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    """After each db-marked test, empty every table."""
    if request.node.get_closest_marker("db") is None:
        yield
        return
    url: str = request.getfixturevalue("db_schema")
    yield
    tables = [t.name for t in Base.metadata.sorted_tables]
    if not tables:
        return
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            quoted = ", ".join(f'"{name}"' for name in tables)
            await conn.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(db_schema: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(db_schema)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
def db_app(db_schema: str) -> FastAPI:
    return create_app(make_settings(database_url=db_schema))


@pytest.fixture
async def db_client(db_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=db_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
