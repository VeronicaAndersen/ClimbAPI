"""
Login throttle shared by all instances, kept in Postgres
(LOGIN_THROTTLE_BACKEND=postgres).

Same limits and the same Reservation flow as the in-memory LoginThrottle, but
the counters live in the login_throttle_key table and the check-and-reserve
runs inside login_throttle_acquire() under transaction-scoped advisory locks
(db/migrations/20261118000000_add_login_throttle.sql). Rules that only depend
on the typed name or the client (normalize_username, ipv6_site_key, the NFKC
key cap in authenticate_user) stay in Python and are shared with the memory
backend.

Every call uses its own connection in autocommit, never the request's
session: a reservation must be visible to other instances before the
password hash runs, a rollback of the request must not undo a recorded
failure, and the advisory locks end with the statement (no transaction
outlives a PgBouncer checkout). A login makes one acquire and one finish
round trip; the load query adds at most one per LOGIN_THROTTLE_LOAD_CACHE_
SECONDS per instance.

Retention: usernames and client keys are stored in plain text (owner's
choice). Expired rows are deleted in batches of LOGIN_THROTTLE_SWEEP_BATCH
during logins, on every acquire and finish; on an idle app they stay until
the next login, and deleted rows remain in Neon's restore history for its
retention period.

Pressure and capacity come from login_throttle_load(), cached per instance.
They can therefore lag by up to the cache time, and the table can overshoot
max_tracked by roughly the attack rate times that time. The bound now
protects table size rather than process memory.

Failure modes (owner decision: 429 with Retry-After, never 503):

* acquire times out (LOGIN_THROTTLE_DB_TIMEOUT_SECONDS), hits a lock
  timeout or a database error: the login is refused with 429 and
  FAIL_CLOSED_RETRY_AFTER_SECONDS. One retry when the connection was
  invalidated (a retry after the first call committed reserves twice, the
  safe side).
* the load query fails or takes longer than a third of the timeout:
  pressure is assumed on and capacity not full, cached like a normal
  reading, so the following logins do not wait for it again.
* finish fails: logged and ignored. The reservation already counts as a
  failure for the client and the /48, so the result errs on the safe side;
  only the approximate per-username count can miss a failure. After a
  release, authenticate_user still re-raises the original error.
* cancelled requests: the reservation stays counted, as in memory.

Warnings are rate-limited and never include usernames or addresses.
"""

import asyncio
import logging
import math
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from schema.climber import normalize_username
from security.client_address import ipv6_site_key
from security.login_ip_limit_postgres import (
    FAIL_CLOSED_RETRY_AFTER_SECONDS,
    lock_timeout_ms,
)
from security.login_throttle import Reservation
from security.rate_limited_warning import RateLimitedWarning

logger = logging.getLogger(__name__)

_KINDS = (1, 2, 3)  # (username, client), username, (username, /48)
_FAILED, _SUCCEEDED, _RELEASED = 1, 2, 3

_ACQUIRE = text(
    "SELECT reserved_at, retry_after, blocked_by_capacity, blocked_by_client_cap "
    "FROM public.login_throttle_acquire("
    "CAST(:username AS text), CAST(:client AS text), CAST(:site AS text), "
    "CAST(:window AS interval), CAST(:max_failures AS integer), "
    "CAST(:max_per_username AS integer), CAST(:max_per_prefix AS integer), "
    "CAST(:max_usernames_per_client AS integer), CAST(:under_pressure AS boolean), "
    "CAST(:full_kinds AS smallint[]), CAST(:lock_timeout_ms AS integer), "
    "CAST(:sweep_batch AS integer), CAST(:now AS timestamptz))"
)
_FINISH = text(
    "SELECT public.login_throttle_finish("
    "CAST(:outcome AS smallint), CAST(:username AS text), CAST(:client AS text), "
    "CAST(:site AS text), CAST(:at AS timestamptz), CAST(:window AS interval), "
    "CAST(:max_per_username AS integer), CAST(:full_kinds AS smallint[]), "
    "CAST(:lock_timeout_ms AS integer), CAST(:sweep_batch AS integer), "
    "CAST(:now AS timestamptz))"
)
_LOAD = text(
    "SELECT key_kind, active_keys FROM public.login_throttle_load("
    "coalesce(CAST(:now AS timestamptz), clock_timestamp()) - CAST(:window AS interval), "
    "CAST(:limit AS integer))"
)

_DATABASE_ERRORS = (TimeoutError, SQLAlchemyError, OSError)


class PostgresLoginThrottle:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        max_failures: int,
        window_seconds: float,
        max_failures_per_username: int,
        max_usernames_per_client: int,
        max_failures_per_prefix: int,
        max_tracked: int = 200_000,
        pressure_ratio: float = 0.1,
        db_timeout_seconds: float = 3.0,
        load_cache_seconds: float = 5.0,
        sweep_batch: int = 200,
        enabled: bool = True,
        now: Callable[[], datetime | None] = lambda: None,
        monotonic: Callable[[], float] = time.monotonic,
        warning_interval_seconds: float = 60.0,
    ) -> None:
        self._engine = engine.execution_options(isolation_level="AUTOCOMMIT")
        self.max_failures = max_failures
        self.max_failures_per_username = max_failures_per_username
        self.max_usernames_per_client = max_usernames_per_client
        self.max_failures_per_prefix = max_failures_per_prefix
        self.max_tracked = max_tracked
        self.window_seconds = window_seconds
        self.enabled = enabled
        # Key count from which the per-client username cap applies.
        self.pressure_threshold = math.ceil(pressure_ratio * max_tracked)
        self._window = timedelta(seconds=window_seconds)
        self._db_timeout = db_timeout_seconds
        # The load reading is optional (a failure means pressure on), so it
        # gets a shorter limit and cannot use up a login's whole time budget.
        self._load_timeout = db_timeout_seconds / 3
        self._lock_timeout_ms = lock_timeout_ms(db_timeout_seconds)
        self._load_cache_seconds = load_cache_seconds
        self._sweep_batch = sweep_batch
        # The database clock is used unless a test injects one (p_now).
        self._now = now
        self._monotonic = monotonic
        # Cached active-key counts per kind; None after a failed load query.
        self._load: dict[int, int] | None = None
        self._load_at = -math.inf

        def warning(message: str) -> RateLimitedWarning:
            return RateLimitedWarning(
                logger, message, interval_seconds=warning_interval_seconds, clock=monotonic
            )

        # No usernames or addresses in these: the count is enough to spot an attack.
        self._warn_unavailable = warning(
            "Login throttle database unavailable (error or timeout); refusing logins with 429"
        )
        self._warn_load_failed = warning(
            "Login throttle load query failed; assuming pressure on, capacity not full"
        )
        self._warn_finish_failed = warning(
            "Login throttle could not record a login outcome (error or timeout)"
        )
        self._warn_full = warning(
            "Login throttle is full of active entries; refusing new usernames"
        )
        self._warn_client_cap = warning(
            "Login throttle refused a client over its distinct-username cap"
        )

    async def acquire(self, username: str, client_ip: str) -> Reservation | int:
        """See LoginThrottle.acquire; 429 (fail closed) if the database fails."""
        username = normalize_username(username)
        if not self.enabled:
            return Reservation(username, client_ip, None)
        # Never raises for database trouble: a failed or slow load means
        # pressure on and capacity not full (owner decision).
        load = await self._current_load()
        try:
            row = await self._call_acquire(username, client_ip, load)
        except _DATABASE_ERRORS as error:
            logger.debug("Login throttle acquire failed (%s)", type(error).__name__)
            self._warn_unavailable()
            return FAIL_CLOSED_RETRY_AFTER_SECONDS
        if row.blocked_by_capacity:
            self._warn_full()
        if row.blocked_by_client_cap:
            self._warn_client_cap()
        if row.retry_after is not None:
            return int(row.retry_after)
        return Reservation(username, client_ip, row.reserved_at)

    async def failed(self, reservation: Reservation) -> None:
        await self._finish(_FAILED, reservation)

    async def succeeded(self, reservation: Reservation) -> None:
        await self._finish(_SUCCEEDED, reservation)

    async def release(self, reservation: Reservation) -> None:
        await self._finish(_RELEASED, reservation)

    def clear(self) -> None:
        """
        Forget the cached load. The table itself is cleared by the test
        harness (TRUNCATE); there is no synchronous way to do it here.
        """
        self._load = None
        self._load_at = -math.inf

    async def _current_load(self) -> dict[int, int] | None:
        """Active keys per kind, cached; None if the query failed."""
        if self._monotonic() - self._load_at < self._load_cache_seconds:
            return self._load
        try:
            rows = await self._execute(
                _LOAD,
                {"now": self._now(), "window": self._window, "limit": self.max_tracked},
                timeout=self._load_timeout,
            )
            load: dict[int, int] | None = {int(k): int(n) for k, n in rows}
        except _DATABASE_ERRORS as error:
            # A timeout too: cached like any failure, so the following logins
            # do not each wait for the slow query again.
            logger.debug("Login throttle load query failed (%s)", type(error).__name__)
            self._warn_load_failed()
            load = None
        self._load = load
        self._load_at = self._monotonic()
        return load

    async def _execute(
        self,
        statement,
        params: dict,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> list:
        """
        Run one statement on its own autocommit connection, within a time limit.

        If the limit (or a cancellation of the request) interrupts the query,
        the connection is invalidated before it is released: the driver may
        be left mid-protocol, and such a connection must never go back into
        the pool for the next login.
        """
        if deadline is None:
            deadline = asyncio.get_running_loop().time() + (timeout or self._db_timeout)
        connection = None
        try:
            async with asyncio.timeout_at(deadline):
                connection = await self._engine.connect()
                result = await connection.execute(statement, params)
                return list(result.all())
        except (TimeoutError, asyncio.CancelledError):
            if connection is not None:
                await connection.invalidate()
            raise
        finally:
            if connection is not None:
                await connection.close()

    def _under_pressure(self, load: dict[int, int] | None) -> bool:
        if load is None:
            return True  # owner decision: a failed load query means pressure on
        return any(load.get(kind, 0) >= self.pressure_threshold for kind in _KINDS)

    def _full_kinds(self, load: dict[int, int] | None) -> list[int]:
        if load is None:
            return []  # ... and capacity not full
        return [kind for kind in _KINDS if load.get(kind, 0) >= self.max_tracked]

    async def _call_acquire(self, username: str, client_ip: str, load: dict[int, int] | None):
        params = {
            "username": username,
            "client": client_ip,
            "site": ipv6_site_key(client_ip),
            "window": self._window,
            "max_failures": self.max_failures,
            "max_per_username": self.max_failures_per_username,
            "max_per_prefix": self.max_failures_per_prefix,
            "max_usernames_per_client": self.max_usernames_per_client,
            "under_pressure": self._under_pressure(load),
            "full_kinds": self._full_kinds(load),
            "lock_timeout_ms": self._lock_timeout_ms,
            "sweep_batch": self._sweep_batch,
            "now": self._now(),
        }
        deadline = asyncio.get_running_loop().time() + self._db_timeout
        for attempt in (1, 2):
            try:
                rows = await self._execute(_ACQUIRE, params, deadline=deadline)
                return rows[0]
            except DBAPIError as error:
                # A stale pooled connection: retry once on a fresh one.
                if attempt == 1 and error.connection_invalidated:
                    continue
                raise
        raise AssertionError("unreachable")  # pragma: no cover

    async def _finish(self, outcome: int, reservation: Reservation) -> None:
        if not self.enabled or reservation.at is None:
            return
        if not isinstance(reservation.at, datetime):
            raise TypeError("PostgresLoginThrottle got a reservation from another backend")
        params = {
            "outcome": outcome,
            "username": reservation.username,
            "client": reservation.client_ip,
            "site": ipv6_site_key(reservation.client_ip),
            "at": reservation.at,
            "window": self._window,
            "max_per_username": self.max_failures_per_username,
            # Cached only: recording an outcome must not add a load query.
            "full_kinds": self._full_kinds(self._load),
            "lock_timeout_ms": self._lock_timeout_ms,
            "sweep_batch": self._sweep_batch,
            "now": self._now(),
        }
        try:
            await self._execute(_FINISH, params, timeout=self._db_timeout)
        except _DATABASE_ERRORS as error:
            logger.debug("Login throttle finish failed (%s)", type(error).__name__)
            self._warn_finish_failed()
