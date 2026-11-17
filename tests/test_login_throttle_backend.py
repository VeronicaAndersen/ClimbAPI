"""The async login-throttle interface and its in-memory backend."""

import pytest

from security.login_throttle import (
    LoginThrottle,
    MemoryLoginThrottle,
    Reservation,
    create_login_throttle,
    login_throttle,
)
from security.rate_limit import RateLimitSettings

IP = "198.51.100.1"


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make() -> MemoryLoginThrottle:
    return MemoryLoginThrottle(
        LoginThrottle(
            3,
            60.0,
            max_failures_per_username=10,
            max_usernames_per_client=1000,
            max_failures_per_prefix=6,
            clock=FakeClock(),
        )
    )


def run_without_suspending(coroutine):
    """Run a coroutine that must finish without ever yielding to the loop."""
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    coroutine.close()
    raise AssertionError("the coroutine suspended: it is not atomic")


def test_singleton_is_the_memory_backend_by_default():
    assert isinstance(login_throttle, MemoryLoginThrottle)


def test_factory_builds_memory_backend_from_settings():
    backend = create_login_throttle(RateLimitSettings(LOGIN_MAX_FAILURES=7))
    assert isinstance(backend, MemoryLoginThrottle)
    assert backend.throttle.max_failures == 7


@pytest.mark.parametrize("name", ["postgres", "shadow"])
def test_factory_refuses_backends_not_implemented_yet(name):
    with pytest.raises(RuntimeError, match="not implemented yet"):
        create_login_throttle(RateLimitSettings(LOGIN_THROTTLE_BACKEND=name))


def test_every_method_completes_without_suspending():
    """The check-and-reserve must stay atomic on the event loop."""
    backend = make()
    reservation = run_without_suspending(backend.acquire("alice", IP))
    assert isinstance(reservation, Reservation)
    run_without_suspending(backend.failed(reservation))
    second = run_without_suspending(backend.acquire("alice", IP))
    run_without_suspending(backend.release(second))
    third = run_without_suspending(backend.acquire("alice", IP))
    run_without_suspending(backend.succeeded(third))


async def test_adapter_keeps_the_throttle_behavior():
    backend = make()
    for _ in range(3):
        await backend.failed(await backend.acquire("alice", IP))
    assert isinstance(await backend.acquire("alice", IP), int)
    backend.clear()
    assert isinstance(await backend.acquire("alice", IP), Reservation)


def test_enabled_passes_through():
    backend = make()
    backend.enabled = False
    assert backend.throttle.enabled is False
    assert backend.enabled is False
    reservation = run_without_suspending(backend.acquire("alice", IP))
    assert reservation.at is None
