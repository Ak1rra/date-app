from app.security.ratelimit import SlidingWindowLimiter
from app.security.tokens import hash_token, new_token


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_limit_then_blocks_with_retry_after() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(3, 60, clock=clock)
    assert [limiter.hit("k") for _ in range(3)] == [None, None, None]
    assert limiter.hit("k") == 60
    clock.now += 25
    assert limiter.hit("k") == 35  # oldest hit leaves the window in 35 s


def test_window_slides() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(2, 10, clock=clock)
    assert limiter.hit("k") is None
    clock.now += 6
    assert limiter.hit("k") is None
    assert limiter.hit("k") == 4
    clock.now += 4  # first hit now exactly out of the window
    assert limiter.hit("k") is None
    assert limiter.hit("k") is not None


def test_rejected_hits_are_not_recorded() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(1, 10, clock=clock)
    assert limiter.hit("k") is None
    for _ in range(5):
        assert limiter.hit("k") == 10
    clock.now += 10
    assert limiter.hit("k") is None


def test_keys_are_independent_and_reset_clears() -> None:
    limiter = SlidingWindowLimiter(1, 60, clock=Clock())
    assert limiter.hit("a") is None
    assert limiter.hit("b") is None
    assert limiter.hit("a") is not None
    limiter.reset()
    assert limiter.hit("a") is None


def test_retry_after_rounds_up_and_is_at_least_one() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(1, 10, clock=clock)
    limiter.hit("k")
    clock.now += 9.2
    assert limiter.hit("k") == 1


def test_tokens() -> None:
    a, b = new_token(), new_token()
    assert a != b
    assert len(a) >= 43
    digest = hash_token(a)
    assert len(digest) == 64
    assert digest == hash_token(a)
    assert digest != hash_token(b)
