"""Async engine and session plumbing. The engine lives on `app.state` (see main.create_app)."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


def make_engine(settings: Settings) -> AsyncEngine:
    # Lazy: no connection is opened until first use.
    return create_async_engine(
        settings.database_url, pool_pre_ping=True, connect_args={"timeout": 5}
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Per-request session. Services commit explicitly; nothing is committed implicitly."""
    async with request.app.state.sessionmaker() as session:
        yield session
