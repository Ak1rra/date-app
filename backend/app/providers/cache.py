"""Provider response cache on the provider_cache table (COST-1).

The key is the sha256 of the normalized request, so near-identical requests share an entry:
coordinates to 3 decimal places (~110 m), times bucketed by the caller with `bucket_time`
(route departures to 15 minutes, weather windows to the hour). Cache only what the provider's
terms allow, and for no longer than the TTL the settings give.
"""

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.ai import ProviderCache

logger = logging.getLogger("app.providers")

SessionFactory = async_sessionmaker[AsyncSession]


class RunMemo(dict[str, Any]):
    """An in-memory memo of Google responses for one AI run, which may not be persisted (Google
    Maps Platform terms). It lives as long as the Providers bundle it is attached to and is never
    shared across requests."""


def bucket_time(moment: datetime, minutes: int) -> datetime:
    """Round `moment` down to a multiple of `minutes` since midnight (UTC if it has a zone)."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    total = moment.hour * 60 + moment.minute
    floored = total - total % minutes
    return moment.replace(hour=floored // 60, minute=floored % 60, second=0, microsecond=0)


def _normalize(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _normalize(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, bool) or value is None or isinstance(value, int | str):
        return value
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value.isoformat()
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, set | frozenset):
        return sorted(_normalize(v) for v in value)
    if isinstance(value, list | tuple):
        return [_normalize(v) for v in value]
    raise TypeError(f"cannot build a cache key from {type(value).__name__}")


def cache_key(request: dict[str, Any]) -> str:
    """sha256 hex of the normalized request: floats (coordinates) rounded to 3 decimal places,
    datetimes in UTC, dataclasses as dicts, keys sorted."""
    return hashlib.sha256(json.dumps(_normalize(request), sort_keys=True).encode()).hexdigest()


def _identity(value: Any) -> Any:
    return value


async def cached[T](
    session_factory: SessionFactory,
    provider: str,
    request: dict[str, Any],
    ttl: int,
    fetch: Callable[[], Awaitable[T]],
    *,
    encode: Callable[[Any], Any] = _identity,
    decode: Callable[[Any], Any] = _identity,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> T:
    """Return the cached answer for `request`, else `fetch()` it, store it for `ttl` seconds and
    return it. `encode`/`decode` turn the fetched value into JSON and back (see
    app.providers.types). A cache that cannot be read or written (database down, an entry that
    no longer decodes) is skipped with a warning rather than failing the call; `fetch` errors
    (ProviderUnavailable) propagate and are never cached. `ttl <= 0` skips the cache."""
    if ttl <= 0:
        return await fetch()
    key = cache_key(request)
    now = clock()
    try:
        async with session_factory() as session:
            payload = (
                await session.execute(
                    select(ProviderCache.payload).where(
                        ProviderCache.provider == provider,
                        ProviderCache.key == key,
                        ProviderCache.expires_at > now,
                    )
                )
            ).scalar_one_or_none()
        if payload is not None:
            return cast(T, decode(payload["v"]))
    except Exception:
        logger.warning("provider cache read failed", extra={"provider": provider}, exc_info=True)

    value = await fetch()
    try:
        expires_at = now + timedelta(seconds=ttl)
        stmt = pg_insert(ProviderCache).values(
            provider=provider, key=key, payload={"v": encode(value)}, expires_at=expires_at
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ProviderCache.provider, ProviderCache.key],
            set_={
                "payload": stmt.excluded.payload,
                "expires_at": stmt.excluded.expires_at,
                "created_at": func.now(),
            },
        )
        async with session_factory() as session:
            await session.execute(stmt)
            await session.commit()
    except Exception:
        logger.warning("provider cache write failed", extra={"provider": provider}, exc_info=True)
    return value
