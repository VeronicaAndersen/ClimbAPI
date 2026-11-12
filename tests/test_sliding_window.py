"""Unit tests for security/sliding_window.py"""

from security.sliding_window import SlidingWindowLog


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make(window=60.0, max_keys=100, max_per_key=5):
    clock = FakeClock()
    removed: list[str] = []
    log: SlidingWindowLog[str] = SlidingWindowLog(
        window,
        max_keys=max_keys,
        max_per_key=max_per_key,
        clock=clock,
        on_remove=removed.append,
    )
    return log, clock, removed


def test_withdraw_one_drops_oldest_and_keeps_latest():
    log, clock, _ = make()
    log.add("a")
    clock.now += 10
    log.add("a")
    log.withdraw_one("a")
    assert log.recent("a") == [1010.0]


def test_withdraw_one_last_attempt_removes_key_with_hook():
    log, _, removed = make()
    log.add("a")
    log.withdraw_one("a")
    assert "a" not in log
    assert removed == ["a"]


def test_withdraw_one_unknown_key_is_a_no_op():
    log, _, removed = make()
    log.withdraw_one("a")
    assert removed == []


def test_recent_drops_out_of_order_expired_key_instead_of_crashing():
    """Defensive path: a stale key behind an active one is removed, not indexed past."""
    log, clock, removed = make(window=60.0)
    log.add("fresh")
    # Simulate a key whose latest timestamp is older than its position says.
    log._entries["stale"] = [clock.now - 10]
    clock.now += 55  # "stale" expired, "fresh" (front) still active
    assert log.recent("stale") is None
    assert "stale" not in log
    assert removed == ["stale"]


def test_withdraw_one_ignores_timestamps_that_expired_in_flight():
    """
    Regression: the expired oldest timestamp was withdrawn instead of the
    reservation, which then stayed counted for a whole extra window.
    """
    log, clock, removed = make(window=60.0)
    log.add("a")  # t=1000, a real failure
    clock.now = 1050
    log.add("a")  # t=1050, the reservation
    clock.now = 1070  # the t=1000 failure expires while the request runs
    log.withdraw_one("a")
    assert "a" not in log
    assert removed == ["a"]


def test_withdraw_one_on_fully_expired_key_removes_it_once():
    log, clock, removed = make(window=60.0)
    log.add("a")
    clock.now += 61
    log.withdraw_one("a")
    assert "a" not in log
    assert removed == ["a"]


def test_on_add_called_once_per_new_key():
    added: list[str] = []
    clock = FakeClock()
    log: SlidingWindowLog[str] = SlidingWindowLog(
        60.0, max_keys=10, max_per_key=5, clock=clock, on_add=added.append
    )
    log.add("a")
    log.add("a")
    clock.now += 61
    log.add("a")  # expired, so created again
    assert added == ["a", "a"]
