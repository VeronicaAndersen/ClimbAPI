"""Unit tests for security/login_throttle.py"""

from security.login_throttle import LoginThrottle


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make(max_attempts=3, window=60.0, **kwargs):
    clock = FakeClock()
    return LoginThrottle(max_attempts, window, clock=clock, **kwargs), clock


def test_retry_after_none_below_limit():
    throttle, _ = make()
    throttle.register_attempt("alice")
    throttle.register_attempt("alice")
    assert throttle.retry_after("alice") is None


def test_retry_after_set_at_limit():
    throttle, clock = make()
    for _ in range(3):
        throttle.register_attempt("alice")
    clock.now += 20
    assert throttle.retry_after("alice") == 40


def test_attempts_expire_after_window():
    throttle, clock = make()
    for _ in range(3):
        throttle.register_attempt("alice")
    clock.now += 60
    assert throttle.retry_after("alice") is None


def test_sliding_window_releases_oldest_attempt_first():
    throttle, clock = make()
    throttle.register_attempt("alice")
    clock.now += 30
    throttle.register_attempt("alice")
    throttle.register_attempt("alice")
    clock.now += 31
    assert throttle.retry_after("alice") is None


def test_reset_clears_username():
    throttle, _ = make()
    for _ in range(3):
        throttle.register_attempt("alice")
    throttle.reset("ALICE ")
    assert throttle.retry_after("alice") is None


def test_username_is_normalized():
    throttle, _ = make()
    for name in ("Alice", " alice", "ALICE"):
        throttle.register_attempt(name)
    assert throttle.retry_after("alice") is not None


def test_disabled_never_blocks():
    throttle, _ = make(enabled=False)
    for _ in range(10):
        throttle.register_attempt("alice")
    assert throttle.retry_after("alice") is None


def test_memory_is_bounded():
    throttle, _ = make(max_tracked=2)
    throttle.register_attempt("a")
    throttle.register_attempt("b")
    throttle.register_attempt("c")
    assert len(throttle._attempts) == 2
    assert "a" not in throttle._attempts
