"""
Per-username login throttling.

Counts login attempts per username in a sliding window and blocks further
attempts once the limit is reached. A successful login clears the counter, so
only failed attempts count in practice. The attempt is registered *before*
the password check, which keeps concurrent requests from slipping past the
limit while they wait for the hash.

Unknown usernames are tracked exactly like real ones so the throttle cannot
be used to find out which usernames exist.

State lives in process memory (one uvicorn worker). The methods do not await,
so they are safe on the single event loop without a lock.
"""

import math
import time
from collections import deque
from collections.abc import Callable

from security.rate_limit import rate_limit_settings


class LoginThrottle:
    def __init__(
        self,
        max_attempts: int,
        window_seconds: float,
        *,
        enabled: bool = True,
        max_tracked: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.enabled = enabled
        self.max_tracked = max_tracked
        self._clock = clock
        self._attempts: dict[str, deque[float]] = {}

    @staticmethod
    def _key(username: str) -> str:
        return username.strip().lower()

    def _prune(self, key: str, now: float) -> deque[float] | None:
        attempts = self._attempts.get(key)
        if attempts is None:
            return None
        cutoff = now - self.window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if not attempts:
            del self._attempts[key]
            return None
        return attempts

    def retry_after(self, username: str) -> int | None:
        """Seconds until the next attempt is allowed, or None if not blocked."""
        if not self.enabled:
            return None
        now = self._clock()
        attempts = self._prune(self._key(username), now)
        if attempts is None or len(attempts) < self.max_attempts:
            return None
        return max(1, math.ceil(attempts[0] + self.window_seconds - now))

    def register_attempt(self, username: str) -> None:
        if not self.enabled:
            return
        now = self._clock()
        key = self._key(username)
        attempts = self._prune(key, now)
        if attempts is None:
            self._evict_if_full(now)
            attempts = self._attempts[key] = deque()
        attempts.append(now)

    def reset(self, username: str) -> None:
        self._attempts.pop(self._key(username), None)

    def clear(self) -> None:
        self._attempts.clear()

    def _evict_if_full(self, now: float) -> None:
        """Bound memory when many different usernames are tried."""
        if len(self._attempts) < self.max_tracked:
            return
        for key in list(self._attempts):
            self._prune(key, now)
        while len(self._attempts) >= self.max_tracked:
            # Dicts keep insertion order, so this drops the oldest entry.
            del self._attempts[next(iter(self._attempts))]


login_throttle = LoginThrottle(
    max_attempts=rate_limit_settings.LOGIN_MAX_FAILURES,
    window_seconds=rate_limit_settings.LOGIN_FAILURE_WINDOW_SECONDS,
    enabled=rate_limit_settings.ENABLED,
)
