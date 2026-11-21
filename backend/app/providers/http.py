"""Plumbing shared by the real providers: HTTP errors mapped to ProviderUnavailable, plus the
cache and rate limiter wrapped around each real call."""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.providers.cache import RunMemo, SessionFactory, cache_key, cached
from app.providers.ratelimit import ProviderRateLimiter
from app.providers.types import (
    BAD_RESPONSE,
    DOWN,
    NO_KEY,
    NOT_FOUND,
    OVER_QUOTA,
    ProviderUnavailable,
)

logger = logging.getLogger("app.providers")
# httpx logs every request URL at INFO, and some of ours carry the API key in the query string.
logging.getLogger("httpx").setLevel(logging.WARNING)

TIMEOUT_SEC = 10.0


@dataclass(frozen=True)
class Runtime:
    """What a real provider needs beyond its key: where to cache, who limits it, and whose
    call this is. Without one the provider neither caches nor rate limits (unit tests). `memo`
    is the per-run memory for providers whose terms forbid storing responses (Google)."""

    session_factory: SessionFactory | None = None
    limiter: ProviderRateLimiter | None = None
    couple_id: uuid.UUID | None = None
    memo: RunMemo | None = None


def reason_for_status(status: int) -> str:
    if status == 429:
        return OVER_QUOTA
    if status in (401, 403):
        return NO_KEY  # the key was rejected
    if status == 404:
        return NOT_FOUND
    return DOWN


class RealProvider:
    """Base for the httpx-backed providers. Subclasses set `provider` (also the cache
    namespace, at most 32 characters) and call `_json` inside the `fetch` they hand to
    `_cached` (responses the provider's terms let us store) or `_memoized` (those they do not)."""

    provider = ""

    def __init__(
        self, *, client: httpx.AsyncClient | None = None, runtime: Runtime | None = None
    ) -> None:
        self._client = client
        self._runtime = runtime or Runtime()

    async def _json(self, method: str, url: str, **kwargs: Any) -> Any:
        """One request, 10 s timeout. Any failure becomes ProviderUnavailable, with the cause
        dropped: httpx messages and bodies can hold the request URL, and so the API key."""
        try:
            if self._client is not None:
                response = await self._client.request(method, url, timeout=TIMEOUT_SEC, **kwargs)
            else:
                async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as client:
                    response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            logger.warning("%s request failed: %s", self.provider, type(exc).__name__)
            raise ProviderUnavailable(self.provider, DOWN) from None
        if response.status_code >= 400:
            logger.warning("%s returned HTTP %s", self.provider, response.status_code)
            raise ProviderUnavailable(self.provider, reason_for_status(response.status_code))
        try:
            return response.json()
        except ValueError:
            # The provider answered, but not with JSON: a bad response, not an outage.
            raise ProviderUnavailable(self.provider, BAD_RESPONSE) from None

    async def _call[T](self, fetch: Callable[[], Awaitable[T]]) -> T:
        """Rate limit, then `fetch`. A body that does not parse is
        ProviderUnavailable(reason "bad_response"): `fetch` validates it with the provider's
        response models (a pydantic ValidationError is a ValueError), and the other errors a
        parser can raise are caught as well."""
        runtime = self._runtime
        if runtime.limiter is not None:
            runtime.limiter.check(self.provider, runtime.couple_id)
        try:
            return await fetch()
        except (
            KeyError,
            ValueError,
            TypeError,
            AttributeError,
            IndexError,
            ArithmeticError,
        ) as exc:
            # Only the type: a validation error message quotes the body.
            logger.warning(
                "%s returned an unreadable response: %s", self.provider, type(exc).__name__
            )
            raise ProviderUnavailable(self.provider, BAD_RESPONSE) from None

    async def _cached[T](
        self,
        request: dict[str, Any],
        ttl: int,
        fetch: Callable[[], Awaitable[T]],
        *,
        encode: Callable[[Any], Any] | None = None,
        decode: Callable[[Any], Any] | None = None,
    ) -> T:
        """Serve `request` from the database cache, or rate limit then `fetch` and cache it."""
        runtime = self._runtime

        async def guarded() -> T:
            return await self._call(fetch)

        if runtime.session_factory is None:
            return await guarded()
        kwargs: dict[str, Any] = {}
        if encode is not None:
            kwargs["encode"] = encode
        if decode is not None:
            kwargs["decode"] = decode
        return await cached(runtime.session_factory, self.provider, request, ttl, guarded, **kwargs)

    async def _memoized[T](self, request: dict[str, Any], fetch: Callable[[], Awaitable[T]]) -> T:
        """Rate limit then `fetch`, reusing an identical request from this run's memo. Nothing is
        written to the database."""
        memo = self._runtime.memo
        if memo is None:
            return await self._call(fetch)
        key = cache_key({"provider": self.provider, **request})
        if key not in memo:
            memo[key] = await self._call(fetch)
        return memo[key]  # type: ignore[no-any-return]
