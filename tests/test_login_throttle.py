"""Unit tests for security/login_throttle.py"""

from collections import deque

from security.login_throttle import LoginThrottle

IP = "198.51.100.1"
OTHER_IP = "203.0.113.9"


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make(max_failures=3, window=60.0, *, per_username=10, **kwargs):
    clock = FakeClock()
    throttle = LoginThrottle(
        max_failures, window, max_failures_per_username=per_username, clock=clock, **kwargs
    )
    return throttle, clock


def fail(throttle, username="alice", ip=IP, times=1):
    """Failed attempts, as the login endpoint reports them."""
    for _ in range(times):
        assert throttle.acquire(username, ip) is None
        throttle.failed(username)


# ---------------------------------------------------------------------------
# Per (username, IP) failures
# ---------------------------------------------------------------------------

def test_allows_attempts_below_limit():
    throttle, _ = make()
    fail(throttle, times=2)
    assert throttle.acquire("alice", IP) is None


def test_blocks_at_limit_with_retry_after():
    throttle, clock = make()
    fail(throttle, times=3)
    clock.now += 20
    assert throttle.acquire("alice", IP) == 40


def test_blocked_attempt_is_not_recorded():
    throttle, clock = make()
    fail(throttle, times=3)
    for _ in range(5):
        assert throttle.acquire("alice", IP) is not None
    clock.now += 60
    assert throttle.acquire("alice", IP) is None


def test_failures_expire_after_window():
    throttle, clock = make()
    fail(throttle, times=3)
    clock.now += 60
    assert throttle.acquire("alice", IP) is None


def test_sliding_window_releases_oldest_failure_first():
    throttle, clock = make()
    fail(throttle)
    clock.now += 30
    fail(throttle, times=2)
    clock.now += 31
    assert throttle.acquire("alice", IP) is None


def test_success_is_not_counted_as_failure():
    """Regression: correct logins must not use up the failure budget."""
    throttle, _ = make()
    for _ in range(10):
        assert throttle.acquire("alice", IP) is None
        throttle.succeeded("alice", IP)


def test_success_clears_client_failures():
    throttle, _ = make()
    fail(throttle, times=2)
    assert throttle.acquire("alice", IP) is None
    throttle.succeeded("alice", IP)
    fail(throttle, times=3)
    assert throttle.acquire("alice", IP) is not None


def test_failures_on_one_ip_do_not_block_another_ip():
    """Regression: an attacker must not be able to lock out the real user."""
    throttle, _ = make()
    fail(throttle, ip=OTHER_IP, times=3)
    assert throttle.acquire("alice", OTHER_IP) is not None
    assert throttle.acquire("alice", IP) is None


def test_username_is_normalized():
    throttle, _ = make()
    for name in ("Alice", " alice", "ALICE"):
        fail(throttle, username=name)
    assert throttle.acquire("alice", IP) is not None


def test_disabled_never_blocks():
    throttle, _ = make(enabled=False)
    fail(throttle, times=10)


# ---------------------------------------------------------------------------
# Per-username cap across IPs
# ---------------------------------------------------------------------------

def test_username_cap_blocks_ips_that_have_failed():
    throttle, _ = make(max_failures=3, per_username=4)
    fail(throttle, ip="10.0.0.1", times=2)
    fail(throttle, ip="10.0.0.2", times=2)
    assert throttle.acquire("alice", "10.0.0.1") is not None
    assert throttle.acquire("alice", "10.0.0.2") is not None


def test_username_cap_never_blocks_a_clean_ip():
    throttle, _ = make(max_failures=3, per_username=4)
    for i in range(4):
        fail(throttle, ip=f"10.0.0.{i}")
    assert throttle.acquire("alice", IP) is None
    throttle.succeeded("alice", IP)
    assert throttle.acquire("alice", IP) is None


def test_username_cap_gives_fresh_ip_a_single_guess():
    throttle, _ = make(max_failures=3, per_username=4)
    for i in range(4):
        fail(throttle, ip=f"10.0.0.{i}")
    fail(throttle, ip="10.0.1.1")
    assert throttle.acquire("alice", "10.0.1.1") is not None


def test_success_keeps_failures_from_other_ips_counted():
    throttle, _ = make(max_failures=3, per_username=4)
    for i in range(4):
        fail(throttle, ip=f"10.0.0.{i}")
    assert throttle.acquire("alice", IP) is None
    throttle.succeeded("alice", IP)
    assert throttle.acquire("alice", "10.0.0.1") is not None


def test_username_cap_retry_after_is_earliest_release():
    throttle, clock = make(max_failures=3, window=60.0, per_username=2)
    fail(throttle, ip="10.0.0.1")
    clock.now += 10
    fail(throttle, ip="10.0.0.2")
    clock.now += 10
    # 10.0.0.2 failed at t+10; the username drops below its cap when the
    # t+0 failure expires, 40 s from now, before 10.0.0.2's own one does.
    assert throttle.acquire("alice", "10.0.0.2") == 40


# ---------------------------------------------------------------------------
# Bounded memory
# ---------------------------------------------------------------------------

def test_expired_keys_are_dropped_to_make_room():
    throttle, clock = make(max_tracked=2)
    fail(throttle, username="a")
    fail(throttle, username="b")
    clock.now += 61
    fail(throttle, username="c")
    assert len(throttle._by_client) == 1
    assert ("c", IP) in throttle._by_client


def test_full_table_never_evicts_active_counter():
    """Regression: flooding new usernames must not reset an active counter."""
    throttle, _ = make(max_tracked=2)
    fail(throttle, username="victim", times=3)
    assert throttle.acquire("victim", IP) is not None

    for i in range(100):
        throttle.acquire(f"flood{i}", IP)

    assert throttle.acquire("victim", IP) is not None
    assert len(throttle._by_client) == 2


def test_full_table_fails_closed_for_new_keys():
    throttle, clock = make(max_tracked=2)
    fail(throttle, username="a")
    clock.now += 10
    fail(throttle, username="b")
    # "a" is the oldest active key and expires 50 s from now.
    assert throttle.acquire("new", IP) == 50
    # Known keys below the limit are still allowed.
    assert throttle.acquire("a", IP) is None
    clock.now += 60
    assert throttle.acquire("new", IP) is None


def test_refreshed_key_moves_behind_older_keys():
    throttle, clock = make(max_tracked=2)
    fail(throttle, username="a")
    clock.now += 10
    fail(throttle, username="b")
    clock.now += 10
    fail(throttle, username="a")
    clock.now += 51  # "b" (t+10) has expired, "a" (latest t+20) has not
    fail(throttle, username="c")
    assert ("a", IP) in throttle._by_client
    assert ("b", IP) not in throttle._by_client


def test_full_table_does_not_scan_every_key():
    """Regression: a full table must not cost an O(n) prune per new key."""
    throttle, _ = make(max_tracked=1000)
    for i in range(1000):
        fail(throttle, username=f"user{i}")

    calls = 0
    real_popleft = deque.popleft

    class CountingDeque(deque):
        def popleft(self):
            nonlocal calls
            calls += 1
            return real_popleft(self)

    for log in (throttle._by_client, throttle._by_username):
        entries = log._entries
        for key, attempts in list(entries.items()):
            entries[key] = CountingDeque(attempts, maxlen=attempts.maxlen)

    for i in range(100):
        throttle.acquire(f"new{i}", IP)
    assert calls == 0
