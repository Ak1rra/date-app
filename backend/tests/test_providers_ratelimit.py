"""The provider rate limiter: per couple, global, and wired into a real provider."""

import uuid

import httpx
import pytest
from pydantic import ValidationError

from app.providers.http import Runtime
from app.providers.places.google import GooglePlaces
from app.providers.ratelimit import ProviderRateLimiter, SlidingWindowLimiter, shared_limiter
from app.providers.types import LatLng, ProviderUnavailable
from tests.conftest import make_settings

COUPLE_A, COUPLE_B = uuid.uuid4(), uuid.uuid4()


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make(per_couple: int, global_: int, clock: Clock) -> ProviderRateLimiter:
    return ProviderRateLimiter(
        SlidingWindowLimiter(per_couple, 60, clock), SlidingWindowLimiter(global_, 60, clock)
    )


def test_a_couple_is_limited_per_minute_and_others_are_unaffected() -> None:
    clock = Clock()
    limiter = make(2, 100, clock)
    limiter.check("google_places", COUPLE_A)
    limiter.check("google_places", COUPLE_A)
    with pytest.raises(ProviderUnavailable) as info:
        limiter.check("ticketmaster", COUPLE_A)
    assert (info.value.provider, info.value.reason) == ("ticketmaster", "rate_limited")
    limiter.check("google_places", COUPLE_B)  # a different couple has its own window


def test_a_call_the_global_limit_rejects_does_not_use_up_the_couples_quota() -> None:
    clock = Clock()
    limiter = make(2, 2, clock)
    limiter.check("p")
    limiter.check("p")  # the global window is now full
    clock.now = 50
    for _ in range(3):  # however often the couple tries, it is the global limit that says no
        with pytest.raises(ProviderUnavailable):
            limiter.check("p", COUPLE_A)
    clock.now = 61  # the global window has room again; the rejected calls left no trace
    limiter.check("p", COUPLE_A)
    limiter.check("p", COUPLE_A)
    with pytest.raises(ProviderUnavailable):
        limiter.check("p", COUPLE_A)  # the couple's own limit (2) still applies


def test_the_window_slides() -> None:
    clock = Clock()
    limiter = make(1, 100, clock)
    limiter.check("p", COUPLE_A)
    clock.now = 59
    with pytest.raises(ProviderUnavailable):
        limiter.check("p", COUPLE_A)
    clock.now = 61
    limiter.check("p", COUPLE_A)


def test_the_global_limit_spans_couples_and_calls_with_no_couple() -> None:
    clock = Clock()
    limiter = make(10, 3, clock)
    limiter.check("p", COUPLE_A)
    limiter.check("p", COUPLE_B)
    limiter.check("p")  # a background job: no couple, still counted globally
    for couple in (COUPLE_A, COUPLE_B, None):
        with pytest.raises(ProviderUnavailable) as info:
            limiter.check("p", couple)
        assert info.value.reason == "rate_limited"
    limiter.reset()
    limiter.check("p", COUPLE_A)


def test_calls_without_a_couple_ignore_the_per_couple_limit() -> None:
    limiter = make(1, 100, Clock())
    for _ in range(5):
        limiter.check("p")


@pytest.mark.parametrize(
    "name", ["provider_calls_per_couple_per_minute", "provider_calls_global_per_minute"]
)
@pytest.mark.parametrize("value", [0, -1])
def test_a_limit_below_one_is_a_configuration_error(name: str, value: int) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{name: value})
    assert make_settings(**{name: 1}) is not None


def test_the_shared_limiter_is_one_per_set_of_limits() -> None:
    settings = make_settings(
        provider_calls_per_couple_per_minute=7, provider_calls_global_per_minute=11
    )
    assert shared_limiter(settings) is shared_limiter(
        make_settings(provider_calls_per_couple_per_minute=7, provider_calls_global_per_minute=11)
    )
    assert shared_limiter(settings) is not shared_limiter(make_settings())
    limiter = shared_limiter(settings)
    limiter.reset()
    couple = uuid.uuid4()
    for _ in range(7):
        limiter.check("p", couple)
    with pytest.raises(ProviderUnavailable):
        limiter.check("p", couple)
    limiter.reset()


async def test_a_limited_provider_makes_no_request() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"places": []})

    limiter = make(2, 100, Clock())
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    places = GooglePlaces("k", client=client, runtime=Runtime(limiter=limiter, couple_id=COUPLE_A))
    near = LatLng(47.6, -122.3)
    await places.search("a", near, 1000)
    await places.search("b", near, 1000)
    with pytest.raises(ProviderUnavailable) as info:
        await places.search("c", near, 1000)
    assert (info.value.provider, info.value.reason) == ("google_places", "rate_limited")
    assert len(calls) == 2
