import math
import time
from collections import deque
from collections.abc import Callable


class SlidingWindowLimiter:
    """In-process sliding-window limiter: at most `limit` hits per `window_sec` per key."""

    def __init__(
        self, limit: int, window_sec: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window_sec = window_sec
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> int | None:
        """Record a hit. None means allowed; otherwise the seconds until a retry can succeed.

        Rejected hits are not recorded, so waiting out the returned time always works.
        """
        now = self._clock()
        cutoff = now - self.window_sec
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.limit:
            return max(1, math.ceil(hits[0] + self.window_sec - now))
        hits.append(now)
        if len(self._hits) > 10_000:
            self._prune(cutoff)
        return None

    def undo(self, key: str) -> None:
        """Forget the most recent hit for `key`, if there is one (to take back a hit whose
        action did not go ahead)."""
        hits = self._hits.get(key)
        if hits:
            hits.pop()

    def _prune(self, cutoff: float) -> None:
        for key in [k for k, h in self._hits.items() if not h or h[-1] <= cutoff]:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()
