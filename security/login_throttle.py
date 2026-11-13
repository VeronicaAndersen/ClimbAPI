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
* per (username, IPv6 /48), IPv6 clients only: ``max_failures_per_prefix``
  failures block every /64 in that /48 for the username, including fresh
  ones. Without it, an attacker holding a /48 has 65,536 /64s and so that
  many extra guesses once the per-username cap is reached. A success does
  not clear it, like the per-username counter. The lock lasts as long as
  failures keep coming, not one window: about one failure a minute from a
  few /64s in the /48 (or a mobile client that gets a new /64 on every
  reconnect) keeps the username locked for the whole /48 indefinitely;
* per client, distinct usernames with recent failures, only while the
  throttle is under pressure (see below): a client that has failed on
  ``max_usernames_per_client`` different usernames is then refused new
  usernames until one of them expires.

An attacker can therefore lock out a username only for their own client (or
their own /48, for IPv6), and a distributed guesser gets at most one guess per
fresh client once the username cap is hit, and at most
``max_failures_per_prefix`` per /48.

An attempt is reserved as a failure in the (username, client) and, for IPv6,
the (username, /48) counters *before* the password check, so a burst of
concurrent requests cannot slip past those limits while the hashes run.
``acquire`` returns a ``Reservation`` holding the exact timestamp it recorded.
A successful login clears the (username, client) counter and withdraws exactly
its own /48 timestamp, so earlier failures keep their own expiry: they are not
cleared, and repeated successes do not keep them alive either. A server error
hands both reservations back the same way (``release``), so only real
failures count. The per-username counter is still only updated once a check
has failed, so a concurrent burst may overshoot it; that is harmless because
it never blocks a client on its own, but it means that cap is approximate.

Unknown usernames are tracked exactly like real ones so the throttle cannot
be used to find out which usernames exist.

State lives in process memory (one uvicorn worker). The methods do not await,
so they are safe on the single event loop without a lock.

Memory is bounded by ``max_tracked`` keys per counter. Measured across all
counters, a failed login costs about 550-750 bytes from IPv4 and 850 bytes to
1.05 KB from IPv6 (which also fills the /48 counter), for short to
200-character usernames. The default of 200,000 failures in a window is
therefore about 110-150 MB for IPv4 traffic and at most about 210 MB.
Expired keys are dropped as time passes, but a key that still has attempts
inside the window is never evicted: that would reset its counter and let an
attacker flush it by trying many other usernames. When every slot holds an
active key, new keys are refused for everyone (fail closed) until the oldest
expires, and a warning is logged.

The per-client username cap keeps that impractical, but on its own it would
also let 20 typos from a shared address (CGNAT, gym wifi, one IPv6 /64) lock
everyone else there out of new usernames. So it only applies while a counter
holds at least ``pressure_ratio`` of ``max_tracked`` keys (default 10%, i.e.
20,000 keys). Below that there is plenty of room and the cap is not needed;
above it, filling the rest of the table takes failed logins from about 9,000
distinct clients within one window. Those clients are IPv6 /64s, and one /48
holder has 65,536 of them, so in theory one holder can reach pressure or fill
the table alone; the practical bound is that every failed login costs a full
Argon2 hash. A user who has not failed before is only affected while the
table is full, or, under pressure, when their address has already failed on
many usernames.
"""

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from schema.climber import normalize_username
from security.client_address import ipv6_site_key
from security.rate_limit import rate_limit_settings
from security.rate_limited_warning import RateLimitedWarning
from security.sliding_window import SlidingWindowLog

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Reservation:
    """
    A reserved login attempt, handed to ``failed``, ``succeeded`` or
    ``release`` once the password check has finished.

    ``at`` is the timestamp recorded for it, unique per throttle, so exactly
    this attempt can be withdrawn later; None when the throttle is disabled
    and nothing was recorded.
    """

    username: str
    client_ip: str
    at: float | None


class LoginThrottle:
    def __init__(
        self,
        max_failures: int,
        window_seconds: float,
        *,
        max_failures_per_username: int,
        max_usernames_per_client: int,
        max_failures_per_prefix: int,
        enabled: bool = True,
        max_tracked: int = 200_000,
        pressure_ratio: float = 0.1,
        clock: Callable[[], float] = time.monotonic,
        warning_interval_seconds: float = 60.0,
    ) -> None:
        self.max_failures = max_failures
        self.max_failures_per_username = max_failures_per_username
        self.max_usernames_per_client = max_usernames_per_client
        self.max_failures_per_prefix = max_failures_per_prefix
        self.window_seconds = window_seconds
        self.enabled = enabled
        self._clock = clock
        # Timestamp of the latest reservation; each new one is strictly later.
        self._last_reservation_at = -math.inf
        # Key count from which the per-client username cap applies.
        self.pressure_threshold = math.ceil(pressure_ratio * max_tracked)
        # Active (username, client) keys per client. Kept in step with
        # _by_client through its on_add/on_remove hooks, so it costs one int
        # per client and cannot drift if a key expires mid-call.
        self._usernames_per_client: dict[str, int] = {}
        self._by_client: SlidingWindowLog[tuple[str, str]] = SlidingWindowLog(
            window_seconds,
            max_keys=max_tracked,
            max_per_key=max_failures,
            clock=clock,
            on_add=self._count_client_key,
            on_remove=self._forget_client_key,
        )
        self._by_username: SlidingWindowLog[str] = SlidingWindowLog(
            window_seconds,
            max_keys=max_tracked,
            max_per_key=max_failures_per_username,
            clock=clock,
        )
        # (username, IPv6 /48) failures; IPv4 clients never get a key here.
        self._by_prefix: SlidingWindowLog[tuple[str, str]] = SlidingWindowLog(
            window_seconds,
            max_keys=max_tracked,
            max_per_key=max_failures_per_prefix,
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

    def acquire(self, username: str, client_ip: str) -> Reservation | int:
        """
        Reserve a login attempt, counted as a failure unless it succeeds.

        Returns a ``Reservation`` when the attempt may go ahead, or the
        seconds to wait when it is blocked (nothing is recorded then). Pass
        the reservation to ``failed``, ``succeeded`` or ``release`` once the
        password check has finished.
        """
        username = normalize_username(username)
        if not self.enabled:
            return Reservation(username, client_ip, None)
        client_key = (username, client_ip)
        retry_after = self._retry_after(username, client_ip, client_key)
        if retry_after is not None:
            return retry_after
        # One timestamp for both counters, kept in the reservation so exactly
        # this attempt can be withdrawn later. It doubles as the reservation's
        # unique id: if the clock has not moved since the last reservation
        # (coarse or frozen clock), it is nudged to the next float, so
        # withdraw_at can never remove another attempt recorded at the same
        # instant. The nudge is nanoseconds, and keeps timestamps ordered.
        now = self._clock()
        if now <= self._last_reservation_at:
            now = math.nextafter(self._last_reservation_at, math.inf)
        self._last_reservation_at = now
        # Cannot fail: _retry_after checked capacity and nothing awaited since.
        self._by_client.add(client_key, at=now)
        site = ipv6_site_key(client_ip)
        if site is not None:
            # Reserved now, not in failed(): otherwise a burst from many /64s
            # would all pass the /48 check while their hashes run.
            self._by_prefix.add((username, site), at=now)
        return Reservation(username, client_ip, now)

    def failed(self, reservation: Reservation) -> None:
        """The reserved attempt had wrong credentials."""
        if not self.enabled or reservation.at is None:
            return
        # Refused only when the log is full of active usernames; acquire()
        # then blocks new usernames until a slot frees up. The (username,
        # client) and /48 failures were already reserved in acquire().
        self._by_username.add(reservation.username)

    def succeeded(self, reservation: Reservation) -> None:
        """
        The reserved attempt was a correct login: forget this IP's failures.

        Failures from other IPs stay counted against the username, and the
        /48 counter is not cleared either: one success inside a /48 must not
        reset the budget of every other /64 in it. Only this attempt's own
        /48 timestamp is withdrawn.
        """
        if not self.enabled or reservation.at is None:
            return
        # Intended: drop the whole (username, client) key, not just this
        # reservation. Proving the password resets that client's failure count
        # for the username, as a lockout counter normally does.
        self._by_client.discard((reservation.username, reservation.client_ip))
        self._withdraw_prefix_reservation(
            reservation.username, reservation.client_ip, reservation.at
        )

    def release(self, reservation: Reservation) -> None:
        """
        Hand back a reservation whose password check never completed.

        For server-side errors (database, hashing): they say nothing about the
        credentials, so they must not count as a failure. Exactly this
        attempt's timestamps are withdrawn; earlier failures stay.
        """
        if not self.enabled or reservation.at is None:
            return
        client_key = (reservation.username, reservation.client_ip)
        self._by_client.withdraw_at(client_key, reservation.at)
        self._withdraw_prefix_reservation(
            reservation.username, reservation.client_ip, reservation.at
        )

    def _withdraw_prefix_reservation(
        self, username: str, client_ip: str, at: float
    ) -> None:
        site = ipv6_site_key(client_ip)
        if site is not None:
            self._by_prefix.withdraw_at((username, site), at)

    def clear(self) -> None:
        self._by_client.clear()
        self._by_username.clear()
        self._by_prefix.clear()
        self._usernames_per_client.clear()

    def _count_client_key(self, key: tuple[str, str]) -> None:
        client_ip = key[1]
        count = self._usernames_per_client.get(client_ip, 0)
        self._usernames_per_client[client_ip] = count + 1

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
        """
        Seconds until every limit that blocks this attempt has cleared.

        Each limit is checked on its own and the longest wait wins, so a
        client that retries after Retry-After is not refused again by a
        second limit that was hidden behind the first.
        """
        client_failures = self._by_client.recent(client_key)
        if client_failures is None:
            waits = self._new_key_waits(username, client_ip)
        else:
            waits = self._known_key_waits(username, client_failures)
        waits.extend(self._prefix_waits(username, client_ip))
        return max(waits) if waits else None

    def _known_key_waits(
        self, username: str, client_failures: list[float]
    ) -> list[int]:
        """Limits for a (username, client) pair that has recent failures."""
        waits: list[int] = []
        if len(client_failures) >= self.max_failures:
            excess = len(client_failures) - self.max_failures
            waits.append(self._by_client.seconds_until_expiry(client_failures[excess]))

        username_failures = self._by_username.recent(username)
        if (
            username_failures is not None
            and len(username_failures) >= self.max_failures_per_username
        ):
            # Lifted when this client's failures expire (it is then a fresh
            # client, which this cap does not block) or the username drops
            # below its cap, whichever comes first.
            excess = len(username_failures) - self.max_failures_per_username
            waits.append(
                min(
                    self._by_client.seconds_until_expiry(client_failures[-1]),
                    self._by_username.seconds_until_expiry(username_failures[excess]),
                )
            )
        return waits

    def _prefix_waits(self, username: str, client_ip: str) -> list[int]:
        """Block every /64 in the client's /48 once it has too many failures."""
        site = ipv6_site_key(client_ip)
        if site is None:
            return []
        key = (username, site)
        waits: list[int] = []
        full = self._by_prefix.seconds_until_capacity(key)
        if full is not None:
            self._warn_full()
            waits.append(full)
        failures = self._by_prefix.recent(key)
        if failures is not None and len(failures) >= self.max_failures_per_prefix:
            excess = len(failures) - self.max_failures_per_prefix
            waits.append(self._by_prefix.seconds_until_expiry(failures[excess]))
        return waits

    def _under_pressure(self) -> bool:
        return any(
            log.active_count() >= self.pressure_threshold
            for log in (self._by_client, self._by_username, self._by_prefix)
        )

    def _new_key_waits(self, username: str, client_ip: str) -> list[int]:
        """Limits for a (username, client) pair with no recent failures."""
        waits: list[int] = []
        if (
            self._usernames_per_client.get(client_ip, 0)
            >= self.max_usernames_per_client
            and self._under_pressure()
        ):
            self._warn_client_cap()
            # Which of the client's keys expires first is not tracked (that
            # would need a per-client ordered index, about doubling memory),
            # so the window is used as an upper bound.
            waits.append(max(1, math.ceil(self.window_seconds)))
        capacity = [
            wait
            for wait in (
                self._by_client.seconds_until_capacity((username, client_ip)),
                self._by_username.seconds_until_capacity(username),
            )
            if wait is not None
        ]
        if capacity:
            self._warn_full()
            waits.extend(capacity)
        return waits


login_throttle = LoginThrottle(
    max_failures=rate_limit_settings.LOGIN_MAX_FAILURES,
    max_failures_per_username=rate_limit_settings.LOGIN_MAX_FAILURES_PER_USERNAME,
    max_usernames_per_client=rate_limit_settings.LOGIN_MAX_USERNAMES_PER_CLIENT,
    max_failures_per_prefix=rate_limit_settings.LOGIN_MAX_FAILURES_PER_USERNAME_PREFIX,
    max_tracked=rate_limit_settings.LOGIN_THROTTLE_MAX_TRACKED,
    pressure_ratio=rate_limit_settings.LOGIN_THROTTLE_PRESSURE_RATIO,
    window_seconds=rate_limit_settings.LOGIN_FAILURE_WINDOW_SECONDS,
    enabled=rate_limit_settings.ENABLED,
)
