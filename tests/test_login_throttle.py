"""Unit tests for security/login_throttle.py"""

from collections import deque

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


def test_expired_keys_are_dropped_to_make_room():
    throttle, clock = make(max_tracked=2)
    throttle.register_attempt("a")
    throttle.register_attempt("b")
    clock.now += 61
    throttle.register_attempt("c")
    assert len(throttle._attempts) == 1
    assert "c" in throttle._attempts


def test_full_table_never_evicts_active_counter():
    """Regression: flooding new usernames must not reset an active counter."""
    throttle, _ = make(max_tracked=2)
    for _ in range(3):
        throttle.register_attempt("victim")
    assert throttle.retry_after("victim") is not None

    for i in range(100):
        name = f"flood{i}"
        if throttle.retry_after(name) is None:
            throttle.register_attempt(name)

    assert throttle.retry_after("victim") is not None
    assert len(throttle._attempts) == 2


def test_full_table_fails_closed_for_new_keys():
    throttle, clock = make(max_tracked=2)
    throttle.register_attempt("a")
    clock.now += 10
    throttle.register_attempt("b")
    # "a" is the oldest active key and expires 50 s from now.
    assert throttle.retry_after("new") == 50
    # Known keys below the limit are still allowed.
    assert throttle.retry_after("a") is None
    clock.now += 50
    assert throttle.retry_after("new") is None


def test_refreshed_key_moves_behind_older_keys():
    throttle, clock = make(max_tracked=2)
    throttle.register_attempt("a")
    clock.now += 10
    throttle.register_attempt("b")
    clock.now += 10
    throttle.register_attempt("a")
    clock.now += 51  # "b" (t=10) has expired, "a" (latest t=20) has not
    throttle.register_attempt("c")
    assert "a" in throttle._attempts
    assert "b" not in throttle._attempts


def test_full_table_does_not_scan_every_key(monkeypatch):
    """Regression: a full table must not cost an O(n) prune per new key."""
    throttle, _ = make(max_tracked=1000)
    for i in range(1000):
        throttle.register_attempt(f"user{i}")

    calls = 0
    real_popleft = deque.popleft

    class CountingDeque(deque):
        def popleft(self):
            nonlocal calls
            calls += 1
            return real_popleft(self)

    for name, attempts in list(throttle._attempts._entries.items()):
        throttle._attempts._entries[name] = CountingDeque(attempts, maxlen=attempts.maxlen)

    for i in range(100):
        throttle.retry_after(f"new{i}")
    assert calls == 0
