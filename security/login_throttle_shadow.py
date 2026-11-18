"""
Shadow mode for the Postgres login throttle (LOGIN_THROTTLE_BACKEND=shadow).

The in-memory throttle decides every login exactly as with
LOGIN_THROTTLE_BACKEND=memory. The Postgres throttle runs alongside on every
acquire, failed, succeeded and release, so its tables fill as they would in
production, and the two are compared. Postgres never changes a response:

* its answer is never returned: no 429 of its own, no different Retry-After;
* its errors and timeouts are swallowed and counted;
* on an allowed login its acquire runs in a background task while the
  password hash runs; the outcome waits for it at most SHADOW_WAIT_SECONDS,
  and each Postgres outcome call is bounded the same way. A login blocked by
  memory waits at most SHADOW_WAIT_SECONDS for the Postgres comparison.

Postgres keeps its own reservation: ShadowReservation carries the memory
reservation and the Postgres acquire task, so failed/succeeded/release
withdraw exactly what each store recorded. If memory blocks but Postgres
reserved, the Postgres reservation is released (that attempt never runs).

Once a minute at most, one WARNING line with counts only (no usernames or
addresses) summarises the period:

    Login throttle shadow (last 60 s): calls=... postgres_errors=...
    postgres_timeouts=... postgres_blocked_memory_allowed=...
    memory_blocked_postgres_allowed=... slow=...

Cumulative totals are in ShadowLoginThrottle.counts.
"""

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from security.login_throttle import LoginThrottleBackend, Reservation
from security.login_throttle_postgres import (
    FAILED,
    RELEASED,
    SUCCEEDED,
    PostgresLoginThrottle,
)

logger = logging.getLogger(__name__)

# Longest a login waits for the Postgres side at any one point.
SHADOW_WAIT_SECONDS = 0.5
# A Postgres call slower than this is counted as slow (it may still finish).
SHADOW_SLOW_SECONDS = 0.25
# At most one summary line per this many seconds.
SHADOW_LOG_INTERVAL_SECONDS = 60.0

COUNTERS = (
    "calls",
    "postgres_errors",
    "postgres_timeouts",
    "postgres_blocked_memory_allowed",
    "memory_blocked_postgres_allowed",
    "slow",
)


@dataclass(frozen=True, slots=True)
class ShadowReservation(Reservation):
    """The memory reservation (the one that counts) plus the Postgres side."""

    memory: Reservation | None = None
    postgres_task: "asyncio.Task[Reservation | int] | None" = None


class ShadowLoginThrottle:
    def __init__(
        self,
        memory: LoginThrottleBackend,
        postgres: PostgresLoginThrottle,
        *,
        wait_seconds: float = SHADOW_WAIT_SECONDS,
        slow_seconds: float = SHADOW_SLOW_SECONDS,
        log_interval_seconds: float = SHADOW_LOG_INTERVAL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.memory = memory
        self.postgres = postgres
        self._wait = wait_seconds
        self._slow = slow_seconds
        self._log_interval = log_interval_seconds
        self._monotonic = monotonic
        self.counts: Counter[str] = Counter({name: 0 for name in COUNTERS})
        self._since_log: Counter[str] = Counter()
        self._last_log = monotonic()
        # Running Postgres tasks; the event loop only keeps weak references.
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def enabled(self) -> bool:
        return self.memory.enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self.memory.enabled = value
        self.postgres.enabled = value

    async def acquire(self, username: str, client_ip: str) -> Reservation | int:
        decision = await self.memory.acquire(username, client_ip)
        if not self.memory.enabled:
            return decision
        self._count("calls")
        task = self._start(self.postgres.acquire_or_raise(username, client_ip))
        if isinstance(decision, int):
            # Memory blocks: compare now (bounded), and release a Postgres
            # reservation, since this attempt will not run.
            shadow = await self._settle(task)
            if isinstance(shadow, Reservation):
                self._count("memory_blocked_postgres_allowed")
                await self._quietly(self.postgres.finish_or_raise(RELEASED, shadow))
            self._maybe_log()
            return decision
        return ShadowReservation(
            decision.username, decision.client_ip, decision.at, memory=decision, postgres_task=task
        )

    async def failed(self, reservation: Reservation) -> None:
        await self._finish(self.memory.failed, FAILED, reservation)

    async def succeeded(self, reservation: Reservation) -> None:
        await self._finish(self.memory.succeeded, SUCCEEDED, reservation)

    async def release(self, reservation: Reservation) -> None:
        await self._finish(self.memory.release, RELEASED, reservation)

    def clear(self) -> None:
        self.memory.clear()
        self.postgres.clear()

    async def drain(self) -> None:
        """Wait for Postgres tasks still running (for tests and shutdown)."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=self._wait)

    async def _finish(
        self,
        memory_outcome: Callable[[Reservation], Awaitable[None]],
        outcome: int,
        reservation: Reservation,
    ) -> None:
        if not isinstance(reservation, ShadowReservation) or reservation.memory is None:
            await memory_outcome(reservation)
            return
        # Memory first: it is the record that decides later logins.
        await memory_outcome(reservation.memory)
        shadow = await self._settle(reservation.postgres_task)
        if isinstance(shadow, int):
            self._count("postgres_blocked_memory_allowed")
        elif isinstance(shadow, Reservation):
            await self._quietly(self.postgres.finish_or_raise(outcome, shadow))
        self._maybe_log()

    def _start(self, call: Awaitable[Any]) -> "asyncio.Task[Any]":
        task = asyncio.ensure_future(self._timed(call))
        self._tasks.add(task)
        task.add_done_callback(self._forget)
        return task

    def _forget(self, task: "asyncio.Task[Any]") -> None:
        self._tasks.discard(task)
        # Mark any exception as retrieved; it was counted where it mattered.
        if not task.cancelled():
            task.exception()

    async def _timed(self, call: Awaitable[Any]) -> Any:
        started = time.monotonic()
        try:
            return await call
        finally:
            if time.monotonic() - started > self._slow:
                self._count("slow")

    async def _settle(self, task: "asyncio.Task[Any] | None") -> Reservation | int | None:
        """The Postgres acquire's answer, or None after an error or timeout."""
        if task is None:
            return None
        done, _ = await asyncio.wait({task}, timeout=self._wait)
        if not done:
            task.cancel()
            self._count("postgres_timeouts")
            return None
        return self._result(task)

    def _result(self, task: "asyncio.Task[Any]") -> Reservation | int | None:
        if task.cancelled():
            self._count("postgres_timeouts")
            return None
        error = task.exception()
        if error is None:
            return task.result()
        self._count("postgres_timeouts" if isinstance(error, TimeoutError) else "postgres_errors")
        return None

    async def _quietly(self, call: Awaitable[None]) -> None:
        """A Postgres outcome call: bounded, errors counted, never raised."""
        try:
            await asyncio.wait_for(self._timed(call), timeout=self._wait)
        except TimeoutError:
            self._count("postgres_timeouts")
        except Exception:  # noqa: BLE001 - shadow errors must never reach the login
            self._count("postgres_errors")

    def _count(self, name: str) -> None:
        self.counts[name] += 1
        self._since_log[name] += 1

    def _maybe_log(self) -> None:
        now = self._monotonic()
        if now - self._last_log < self._log_interval or not self._since_log:
            return
        # Counts only: no usernames, no addresses.
        logger.warning(
            "Login throttle shadow (last %d s): %s",
            round(now - self._last_log),
            " ".join(f"{name}={self._since_log[name]}" for name in COUNTERS),
        )
        self._since_log.clear()
        self._last_log = now
