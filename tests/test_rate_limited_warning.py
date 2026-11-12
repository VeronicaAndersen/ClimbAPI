"""Unit tests for security/rate_limited_warning.py"""

import logging

from security.rate_limited_warning import RateLimitedWarning

logger = logging.getLogger("test.rate_limited_warning")


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_logs_first_hit_then_at_most_once_per_interval(caplog):
    clock = FakeClock()
    warn = RateLimitedWarning(logger, "something", interval_seconds=60.0, clock=clock)
    with caplog.at_level(logging.WARNING, logger=logger.name):
        warn()
        clock.now += 59
        warn()
        warn()
        clock.now += 1
        warn()
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "something (1 time(s) since the last warning)",
        "something (3 time(s) since the last warning)",
    ]
