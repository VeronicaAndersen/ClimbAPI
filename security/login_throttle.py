"""
Login throttling on failed attempts, per username.

Two sliding-window counters of failed logins:

* per (username, client IP): ``max_failures`` failures block that IP from
  that username, even with the right password (otherwise a 200 vs 429 answer
  would still tell a guesser when they hit it);
* per username, across all IPs: once ``max_failures_per_username`` is reached,
  only IPs that have themselves failed on the username are blocked.

So an attacker can lock out the username only for their own IP (and anyone
sharing it). The real user on a clean IP can always log in, while a
distributed guesser gets at most one guess per fresh IP once the username cap
is hit.

An attempt is reserved as a (username, IP) failure *before* the password
check, so concurrent requests cannot slip past the limit while they wait for
the hash. A successful login clears that counter, so only failures count. The
per-username counter is only updated once a check has failed; concurrent
requests may overshoot it slightly, which is harmless because it never blocks
an IP on its own.

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
from collections.abc import Callable, Hashable

from schema.climber import normalize_username
from security.rate_limit import rate_limit_settings


class SlidingWindowLog[K: Hashable]:
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
        self._entries: OrderedDict[K, deque[float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: K) -> bool:
        return key in self._entries

    def _drop_expired_keys(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._entries:
            oldest = next(iter(self._entries.values()))
            if oldest[-1] > cutoff:
                return
            self._entries.popitem(last=False)

    def recent(self, key: K) -> deque[float] | None:
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

    def seconds_until_capacity(self, key: K) -> int | None:
        """
        None if ``key`` can be recorded, else seconds until a slot frees up.

        Only a new key can be refused, and only when every slot holds a key
        that is still active.
        """
        if self.recent(key) is not None or len(self._entries) < self.max_keys:
            return None
        oldest = next(iter(self._entries.values()))
        return self._seconds_until(oldest[-1])

    def add(self, key: K) -> bool:
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

    def discard(self, key: K) -> None:
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
        max_failures: int,
        window_seconds: float,
        *,
        max_failures_per_username: int,
        enabled: bool = True,
        max_tracked: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.max_failures_per_username = max_failures_per_username
        self.window_seconds = window_seconds
        self.enabled = enabled
        self._by_client: SlidingWindowLog[tuple[str, str]] = SlidingWindowLog(
            window_seconds, max_keys=max_tracked, max_per_key=max_failures, clock=clock
        )
        self._by_username: SlidingWindowLog[str] = SlidingWindowLog(
            window_seconds,
            max_keys=max_tracked,
            max_per_key=max_failures_per_username,
            clock=clock,
        )

    def acquire(self, username: str, client_ip: str) -> int | None:
        """
        Reserve a login attempt, counted as a failure unless ``succeeded``.

        Returns None when the attempt may go ahead, or the seconds to wait
        when it is blocked (nothing is recorded then). Call ``failed`` or
        ``succeeded`` once the password has been checked.
        """
        if not self.enabled:
            return None
        username = normalize_username(username)
        client_key = (username, client_ip)
        retry_after = self._retry_after(username, client_key)
        if retry_after is not None:
            return retry_after
        # Cannot fail: _retry_after checked capacity and nothing awaited since.
        self._by_client.add(client_key)
        return None

    def failed(self, username: str) -> None:
        """The reserved attempt had wrong credentials."""
        if not self.enabled:
            return
        # Refused only when the log is full of active usernames; acquire()
        # then blocks the username until a slot frees up.
        self._by_username.add(normalize_username(username))

    def succeeded(self, username: str, client_ip: str) -> None:
        """
        The reserved attempt was a correct login: forget this IP's failures.

        Failures from other IPs stay counted against the username.
        """
        if not self.enabled:
            return
        self._by_client.discard((normalize_username(username), client_ip))

    def clear(self) -> None:
        self._by_client.clear()
        self._by_username.clear()

    def _retry_after(self, username: str, client_key: tuple[str, str]) -> int | None:
        for full in (
            self._by_client.seconds_until_capacity(client_key),
            self._by_username.seconds_until_capacity(username),
        ):
            if full is not None:
                return full

        client_failures = self._by_client.recent(client_key)
        if client_failures is None:
            return None
        if len(client_failures) >= self.max_failures:
            return self._by_client.seconds_until_expiry(client_failures[0])

        username_failures = self._by_username.recent(username)
        if (
            username_failures is None
            or len(username_failures) < self.max_failures_per_username
        ):
            return None
        # Blocked until this IP's failures expire or the username drops below
        # its cap, whichever comes first.
        excess = len(username_failures) - self.max_failures_per_username
        return min(
            self._by_client.seconds_until_expiry(client_failures[-1]),
            self._by_username.seconds_until_expiry(username_failures[excess]),
        )


login_throttle = LoginThrottle(
    max_failures=rate_limit_settings.LOGIN_MAX_FAILURES,
    max_failures_per_username=rate_limit_settings.LOGIN_MAX_FAILURES_PER_USERNAME,
    window_seconds=rate_limit_settings.LOGIN_FAILURE_WINDOW_SECONDS,
    enabled=rate_limit_settings.ENABLED,
)
