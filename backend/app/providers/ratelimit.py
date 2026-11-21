"""In-process limits on calls to paid or quota-limited providers (COST-1/5)."""

import threading
import uuid

from app.config import Settings
from app.providers.types import RATE_LIMITED, ProviderUnavailable
from app.security.ratelimit import SlidingWindowLimiter

__all__ = ["ProviderRateLimiter", "SlidingWindowLimiter", "shared_limiter"]

WINDOW_SEC = 60


class ProviderRateLimiter:
    """A per-couple and a global sliding window (calls per minute) over all providers.

    `check` records a call or raises ProviderUnavailable(reason="rate_limited"). Only calls that
    really go out are checked: a cache hit costs nothing. A call with no couple (for example a
    background job) counts against the global window only. A rejected call is recorded in
    neither window.
    """

    def __init__(self, per_couple: SlidingWindowLimiter, global_: SlidingWindowLimiter) -> None:
        self._per_couple = per_couple
        self._global = global_

    def check(self, provider: str, couple_id: uuid.UUID | None = None) -> None:
        key = str(couple_id)
        if couple_id is not None and self._per_couple.hit(key) is not None:
            raise ProviderUnavailable(provider, RATE_LIMITED)
        if self._global.hit("global") is not None:
            if couple_id is not None:
                # No await between the two hits, so nothing else can have hit in between.
                self._per_couple.undo(key)
            raise ProviderUnavailable(provider, RATE_LIMITED)

    def reset(self) -> None:
        self._per_couple.reset()
        self._global.reset()


_SHARED: dict[tuple[int, int], ProviderRateLimiter] = {}
_SHARED_LOCK = threading.Lock()


def shared_limiter(settings: Settings) -> ProviderRateLimiter:
    """The process-wide limiter for these settings' limits, so the windows are shared by every
    provider instance built in this process."""
    limits = (
        settings.provider_calls_per_couple_per_minute,
        settings.provider_calls_global_per_minute,
    )
    # Builders may run on FastAPI's threadpool, so creation is locked: two first calls must
    # never end up with separate windows.
    with _SHARED_LOCK:
        if limits not in _SHARED:
            _SHARED[limits] = ProviderRateLimiter(
                SlidingWindowLimiter(limits[0], WINDOW_SEC),
                SlidingWindowLimiter(limits[1], WINDOW_SEC),
            )
        return _SHARED[limits]
