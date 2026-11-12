"""
Login throttling on failed attempts.

Sliding-window counters of failed logins, keyed by client (an IPv4 address or
an IPv6 /64, see ``security.client_address.client_ip``):

* per (username, client): ``max_failures`` failures block that client from
  that username, even with the right password (otherwise a 200 vs 429 answer
  would still tell a guesser when they hit it);
* per username, across all clients: once ``max_failures_per_username`` is
  reached, only clients that have themselves failed on the username are
  blocked;
* per client, distinct usernames with recent failures: a client that has
  failed on ``max_usernames_per_client`` different usernames is refused new
  usernames until one of them expires.

An attacker can therefore lock out a username only for their own client, and a
distributed guesser gets at most one guess per fresh client once the username
cap is hit.

An attempt is reserved as a (username, client) failure *before* the password
check, so concurrent requests cannot slip past the limit while they wait for
the hash. A successful login clears that counter, and a server error hands the
reservation back (``release``), so only real failures count. The per-username
counter is only updated once a check has failed; concurrent requests may
overshoot it slightly, which is harmless because it never blocks a client on
its own.

Unknown usernames are tracked exactly like real ones so the throttle cannot
be used to find out which usernames exist.

State lives in process memory (one uvicorn worker). The methods do not await,
so they are safe on the single event loop without a lock.

Memory is bounded by ``max_tracked`` keys per counter. Each failed login
costs roughly 600 bytes (short usernames) to 1 KB (200-character usernames)
across both counters, so the default of 200,000 stays around 120-200 MB.
Expired keys are dropped as time passes, but a key that still has attempts
inside the window is never evicted: that would reset its counter and let an
attacker flush it by trying many other usernames. When every slot holds an
active key, new keys are refused for everyone (fail closed) until the oldest
expires, and a warning is logged. The per-client username cap keeps that
impractical: with the defaults, filling the table takes failed logins from
about 10,000 distinct clients within one window. A user who has not failed
before is only affected while the table is full.
"""

import logging
import math
import time
from collections.abc import Callable

from schema.climber import normalize_username
from security.rate_limit import rate_limit_settings
from security.rate_limited_warning import RateLimitedWarning
from security.sliding_window import SlidingWindowLog

logger = logging.getLogger(__name__)


class LoginThrottle:
    def __init__(
        self,
        max_failures: int,
        window_seconds: float,
        *,
        max_failures_per_username: int,
        max_usernames_per_client: int,
        enabled: bool = True,
        max_tracked: int = 200_000,
        clock: Callable[[], float] = time.monotonic,
        warning_interval_seconds: float = 60.0,
    ) -> None:
        self.max_failures = max_failures
        self.max_failures_per_username = max_failures_per_username
        self.max_usernames_per_client = max_usernames_per_client
        self.window_seconds = window_seconds
        self.enabled = enabled
        # Active (username, client) keys per client. Kept in step with
        # _by_client through its on_remove hook, so it costs one int per client.
        self._usernames_per_client: dict[str, int] = {}
        self._by_client: SlidingWindowLog[tuple[str, str]] = SlidingWindowLog(
            window_seconds,
            max_keys=max_tracked,
            max_per_key=max_failures,
            clock=clock,
            on_remove=self._forget_client_key,
        )
        self._by_username: SlidingWindowLog[str] = SlidingWindowLog(
            window_seconds,
            max_keys=max_tracked,
            max_per_key=max_failures_per_username,
            clock=clock,
        )
        # No usernames or addresses in these: the count is enough to spot an attack.
        self._warn_full = RateLimitedWarning(
            logger,
            "Login throttle is full of active entries; refusing new usernames",
            interval_seconds=warning_interval_seconds,
            clock=clock,
        )
        self._warn_client_cap = RateLimitedWarning(
            logger,
            "Login throttle refused a client over its distinct-username cap",
            interval_seconds=warning_interval_seconds,
            clock=clock,
        )

    def acquire(self, username: str, client_ip: str) -> int | None:
        """
        Reserve a login attempt, counted as a failure unless ``succeeded``.

        Returns None when the attempt may go ahead, or the seconds to wait
        when it is blocked (nothing is recorded then). Call ``failed``,
        ``succeeded`` or ``release`` once the password check has finished.
        """
        if not self.enabled:
            return None
        username = normalize_username(username)
        client_key = (username, client_ip)
        retry_after = self._retry_after(username, client_ip, client_key)
        if retry_after is not None:
            return retry_after
        if client_key not in self._by_client:
            self._usernames_per_client[client_ip] = (
                self._usernames_per_client.get(client_ip, 0) + 1
            )
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
        # Intended: drop the whole (username, client) key, not just this
        # reservation. Proving the password resets that client's failure count
        # for the username, as a lockout counter normally does.
        self._by_client.discard((normalize_username(username), client_ip))

    def release(self, username: str, client_ip: str) -> None:
        """
        Hand back a reservation whose password check never completed.

        For server-side errors (database, hashing): they say nothing about the
        credentials, so they must not count as a failure. The count drops by
        one; earlier failures stay (see ``SlidingWindowLog.withdraw_one``).
        """
        if not self.enabled:
            return
        self._by_client.withdraw_one((normalize_username(username), client_ip))

    def clear(self) -> None:
        self._by_client.clear()
        self._by_username.clear()
        self._usernames_per_client.clear()

    def _forget_client_key(self, key: tuple[str, str]) -> None:
        client_ip = key[1]
        remaining = self._usernames_per_client.get(client_ip, 0) - 1
        if remaining > 0:
            self._usernames_per_client[client_ip] = remaining
        else:
            self._usernames_per_client.pop(client_ip, None)

    def _retry_after(
        self, username: str, client_ip: str, client_key: tuple[str, str]
    ) -> int | None:
        client_failures = self._by_client.recent(client_key)
        if client_failures is None:
            return self._new_key_retry_after(username, client_ip)
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

    def _new_key_retry_after(self, username: str, client_ip: str) -> int | None:
        """Checks for a (username, client) pair with no recent failures."""
        if self._usernames_per_client.get(client_ip, 0) >= self.max_usernames_per_client:
            self._warn_client_cap()
            # Which of the client's keys expires first is not tracked; the
            # window is an upper bound.
            return max(1, math.ceil(self.window_seconds))
        for full in (
            self._by_client.seconds_until_capacity((username, client_ip)),
            self._by_username.seconds_until_capacity(username),
        ):
            if full is not None:
                self._warn_full()
                return full
        return None


login_throttle = LoginThrottle(
    max_failures=rate_limit_settings.LOGIN_MAX_FAILURES,
    max_failures_per_username=rate_limit_settings.LOGIN_MAX_FAILURES_PER_USERNAME,
    max_usernames_per_client=rate_limit_settings.LOGIN_MAX_USERNAMES_PER_CLIENT,
    max_tracked=rate_limit_settings.LOGIN_THROTTLE_MAX_TRACKED,
    window_seconds=rate_limit_settings.LOGIN_FAILURE_WINDOW_SECONDS,
    enabled=rate_limit_settings.ENABLED,
)
