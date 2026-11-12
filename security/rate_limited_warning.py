"""A log warning that fires at most once per interval."""

import logging
import time
from collections.abc import Callable


class RateLimitedWarning:
    """
    Log a warning at most once per interval, with how often it was hit.

    For conditions that can repeat on every request (an attack, a proxy
    misconfiguration): one line a minute with a count is enough to notice,
    and it cannot flood the logs. Callers must keep personal data such as
    usernames and IP addresses out of the message.
    """

    def __init__(
        self,
        logger: logging.Logger,
        message: str,
        *,
        interval_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._logger = logger
        self._message = message
        self._interval = interval_seconds
        self._clock = clock
        self._last: float | None = None
        self._suppressed = 0

    def __call__(self) -> None:
        now = self._clock()
        self._suppressed += 1
        if self._last is not None and now - self._last < self._interval:
            return
        self._logger.warning(
            "%s (%d time(s) since the last warning)", self._message, self._suppressed
        )
        self._last = now
        self._suppressed = 0
