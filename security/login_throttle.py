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

Memory is bounded by ``max_tracked`` keys. Expired keys are dropped as time
passes, but a key that still has attempts inside the window is never evicted:
that would reset its counter and let an attacker flush it by trying many other
usernames. When every slot holds an active key, new keys are refused (fail
closed) until the oldest one expires.
"""

import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from schema.climber import normalize_username
from security.rate_limit import rate_limit_settings


class SlidingWindowLog:
    """
    Attempt timestamps per key within a sliding window, bounded in size.

    Keys are kept ordered by their latest timestamp, oldest first. The clock
    is monotonic, so the first key is always the next to expire, and dropping
    expired keys only ever looks at the front: amortized O(1) per call, no
    matter how many keys are tracked.
    """

    def __init__(
        self,
        window_seconds: float,
        *,
        max_keys: int,
        max_per_key: int,
        clock: Callable[[], float],
    ) -> None:
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self.max_per_key = max_per_key
        self._clock = clock
        self._entries: OrderedDict[str, deque[float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: str) -> bool:
        return key in self._entries

    def _drop_expired_keys(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._entries:
            oldest = next(iter(self._entries.values()))
            if oldest[-1] > cutoff:
                return
            self._entries.popitem(last=False)

    def recent(self, key: str) -> deque[float] | None:
        """Timestamps for ``key`` inside the window, oldest first, or None."""
        now = self._clock()
        self._drop_expired_keys(now)
        timestamps = self._entries.get(key)
        if timestamps is None:
            return None
        cutoff = now - self.window_seconds
        # The latest timestamp is inside the window, so this never empties it.
        while timestamps[0] <= cutoff:
            timestamps.popleft()
        return timestamps

    def seconds_until_capacity(self, key: str) -> int | None:
        """
        None if ``key`` can be recorded, else seconds until a slot frees up.

        Only a new key can be refused, and only when every slot holds a key
        that is still active.
        """
        if self.recent(key) is not None or len(self._entries) < self.max_keys:
            return None
        oldest = next(iter(self._entries.values()))
        return self._seconds_until(oldest[-1])

    def add(self, key: str) -> bool:
        """Record an attempt now; False if the log is full of active keys."""
        timestamps = self.recent(key)
        if timestamps is None:
            if len(self._entries) >= self.max_keys:
                return False
            timestamps = self._entries[key] = deque(maxlen=self.max_per_key)
        else:
            self._entries.move_to_end(key)
        timestamps.append(self._clock())
        return True

    def discard(self, key: str) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def seconds_until_expiry(self, timestamp: float) -> int:
        return self._seconds_until(timestamp)

    def _seconds_until(self, timestamp: float) -> int:
        return max(1, math.ceil(timestamp + self.window_seconds - self._clock()))


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
        self._attempts = SlidingWindowLog(
            window_seconds,
            max_keys=max_tracked,
            max_per_key=max_attempts,
            clock=clock,
        )

    @staticmethod
    def _key(username: str) -> str:
        return normalize_username(username)

    def retry_after(self, username: str) -> int | None:
        """Seconds until the next attempt is allowed, or None if not blocked."""
        if not self.enabled:
            return None
        key = self._key(username)
        attempts = self._attempts.recent(key)
        if attempts is None:
            return self._attempts.seconds_until_capacity(key)
        if len(attempts) < self.max_attempts:
            return None
        return self._attempts.seconds_until_expiry(attempts[0])

    def register_attempt(self, username: str) -> None:
        if not self.enabled:
            return
        # Cannot fail after retry_after() returned None with no await between.
        self._attempts.add(self._key(username))

    def reset(self, username: str) -> None:
        self._attempts.discard(self._key(username))

    def clear(self) -> None:
        self._attempts.clear()


login_throttle = LoginThrottle(
    max_attempts=rate_limit_settings.LOGIN_MAX_FAILURES,
    window_seconds=rate_limit_settings.LOGIN_FAILURE_WINDOW_SECONDS,
    enabled=rate_limit_settings.ENABLED,
)
