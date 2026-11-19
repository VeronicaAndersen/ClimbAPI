"""
Shadow mode: memory decides, Postgres runs alongside and never affects the
response. The Postgres side is a controllable fake here; tests/pg runs the
real one.
"""

import asyncio
import logging
from datetime import UTC, datetime

import pytest

from security.login_throttle import LoginThrottle, MemoryLoginThrottle, Reservation
from security.login_throttle_postgres import FAILED, RELEASED, SUCCEEDED
from security.login_throttle_shadow import ShadowLoginThrottle, ShadowReservation

USERNAME = "secret-user-name"
IP = "203.0.113.77"


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakePostgres:
    """Stands in for PostgresLoginThrottle's raising methods."""

    def __init__(self, acquire="allow", finish="ok", delay=0.0):
        self.enabled = True
        self.acquire_mode = acquire
        self.finish_mode = finish
        self.delay = delay
        self.finished: list[tuple[int, Reservation]] = []
        self.cleared = False

    async def acquire_or_raise(self, username, client_ip):
        await asyncio.sleep(self.delay)
        if self.acquire_mode == "error":
            raise OSError("database down")
        if self.acquire_mode == "timeout":
            raise TimeoutError
        if self.acquire_mode == "block":
            return 999
        return Reservation(username, client_ip, datetime(2026, 1, 1, tzinfo=UTC))

    async def finish_or_raise(self, outcome, reservation):
        if self.finish_mode == "error":
            raise OSError("database down")
        if self.finish_mode == "hang":
            await asyncio.sleep(10)
        if self.finish_mode == "slow":
            await asyncio.sleep(0.3)
        self.finished.append((outcome, reservation))

    def clear(self):
        self.cleared = True


def make(postgres: FakePostgres, *, max_failures=3, **kwargs):
    clock = FakeClock()
    memory = MemoryLoginThrottle(
        LoginThrottle(
            max_failures,
            60.0,
            max_failures_per_username=100,
            max_usernames_per_client=100,
            max_failures_per_prefix=100,
            clock=clock,
        )
    )
    options = {"wait_seconds": 0.2, "slow_seconds": 0.1, "monotonic": clock}
    options.update(kwargs)
    return ShadowLoginThrottle(memory, postgres, **options), clock


async def memory_only_results(attempts: int, max_failures=3) -> list:
    """What the memory backend alone answers for the same attempts."""
    memory = MemoryLoginThrottle(
        LoginThrottle(
            max_failures,
            60.0,
            max_failures_per_username=100,
            max_usernames_per_client=100,
            max_failures_per_prefix=100,
            clock=FakeClock(),
        )
    )
    results = []
    for _ in range(attempts):
        result = await memory.acquire(USERNAME, IP)
        results.append(result if isinstance(result, int) else "allowed")
        if isinstance(result, Reservation):
            await memory.failed(result)
    return results


async def run_failures(shadow, attempts: int) -> list:
    results = []
    for _ in range(attempts):
        result = await shadow.acquire(USERNAME, IP)
        results.append(result if isinstance(result, int) else "allowed")
        if isinstance(result, Reservation):
            await shadow.failed(result)
    return results


@pytest.mark.parametrize(
    "postgres",
    [
        FakePostgres(acquire="allow"),
        FakePostgres(acquire="block"),
        FakePostgres(acquire="error"),
        FakePostgres(acquire="timeout"),
        FakePostgres(acquire="allow", finish="error"),
    ],
    ids=["agrees", "blocks", "errors", "times-out", "finish-errors"],
)
async def test_memory_decision_and_retry_after_are_always_returned(postgres):
    shadow, _ = make(postgres)
    assert await run_failures(shadow, 6) == await memory_only_results(6)


async def test_reservation_is_memory_s_with_postgres_kept_separately():
    postgres = FakePostgres()
    shadow, _ = make(postgres)
    reservation = await shadow.acquire(USERNAME, IP)
    assert isinstance(reservation, ShadowReservation)
    assert reservation.memory is not None
    assert (reservation.username, reservation.client_ip, reservation.at) == (
        reservation.memory.username,
        reservation.memory.client_ip,
        reservation.memory.at,
    )
    await shadow.succeeded(reservation)
    ((outcome, postgres_reservation),) = postgres.finished
    assert outcome == SUCCEEDED
    # Postgres got its own reservation, not the memory one.
    assert postgres_reservation.at == datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(("method", "outcome"), [("failed", FAILED), ("release", RELEASED)])
async def test_each_outcome_reaches_postgres(method, outcome):
    postgres = FakePostgres()
    shadow, _ = make(postgres)
    reservation = await shadow.acquire(USERNAME, IP)
    await getattr(shadow, method)(reservation)
    assert [o for o, _ in postgres.finished] == [outcome]


async def test_postgres_error_is_counted_not_raised():
    shadow, _ = make(FakePostgres(acquire="error"))
    await shadow.failed(await shadow.acquire(USERNAME, IP))
    assert shadow.counts["postgres_errors"] == 1
    assert shadow.counts["calls"] == 1


async def test_postgres_timeout_error_is_counted_as_timeout():
    shadow, _ = make(FakePostgres(acquire="timeout"))
    await shadow.failed(await shadow.acquire(USERNAME, IP))
    assert shadow.counts["postgres_timeouts"] == 1
    assert shadow.counts["postgres_errors"] == 0


async def test_finish_error_is_counted_not_raised():
    shadow, _ = make(FakePostgres(finish="error"))
    await shadow.failed(await shadow.acquire(USERNAME, IP))
    assert shadow.counts["postgres_errors"] == 1


async def test_hung_postgres_adds_at_most_the_wait_bound():
    shadow, _ = make(FakePostgres(delay=10), wait_seconds=0.2)
    loop = asyncio.get_running_loop()
    started = loop.time()
    reservation = await shadow.acquire(USERNAME, IP)
    assert loop.time() - started < 0.05  # allowed logins do not wait for Postgres
    started = loop.time()
    await shadow.failed(reservation)
    assert loop.time() - started < 0.4
    assert shadow.counts["shadow_wait_exceeded"] == 1
    assert shadow.counts["postgres_timeouts"] == 0


async def test_hung_postgres_finish_adds_at_most_the_wait_bound():
    shadow, _ = make(FakePostgres(finish="hang"), wait_seconds=0.2)
    reservation = await shadow.acquire(USERNAME, IP)
    loop = asyncio.get_running_loop()
    started = loop.time()
    await shadow.failed(reservation)
    assert loop.time() - started < 0.6
    assert shadow.counts["shadow_wait_exceeded"] == 1
    assert shadow.counts["postgres_timeouts"] == 0


async def test_acquire_slower_than_the_wait_is_not_cancelled_and_is_released_late():
    postgres = FakePostgres(delay=0.3)
    shadow, _ = make(postgres, wait_seconds=0.1)
    reservation = await shadow.acquire(USERNAME, IP)
    await shadow.failed(reservation)  # waits 0.1 s, then gives up waiting
    assert shadow.counts["shadow_wait_exceeded"] == 1
    assert shadow.counts["postgres_timeouts"] == 0
    assert postgres.finished == []
    await asyncio.sleep(0.4)  # the acquire completes and its reservation is handed back
    assert [outcome for outcome, _ in postgres.finished] == [RELEASED]
    assert shadow.counts["postgres_errors"] == 0
    assert not shadow._tasks


async def test_late_acquire_error_is_counted_without_unretrieved_warnings():
    postgres = FakePostgres(acquire="error", delay=0.3)
    shadow, _ = make(postgres, wait_seconds=0.1)
    await shadow.failed(await shadow.acquire(USERNAME, IP))
    assert shadow.counts["shadow_wait_exceeded"] == 1
    assert shadow.counts["postgres_errors"] == 0
    await asyncio.sleep(0.4)
    assert shadow.counts["postgres_errors"] == 1
    assert postgres.finished == []


async def test_slow_outcome_call_is_not_cancelled():
    postgres = FakePostgres(finish="slow")
    shadow, _ = make(postgres, wait_seconds=0.1)
    await shadow.failed(await shadow.acquire(USERNAME, IP))
    assert shadow.counts["shadow_wait_exceeded"] == 1
    await asyncio.sleep(0.4)
    assert [outcome for outcome, _ in postgres.finished] == [FAILED]  # it still completed


async def test_hung_postgres_on_a_memory_blocked_login_is_bounded():
    shadow, _ = make(FakePostgres(delay=10), max_failures=1, wait_seconds=0.2)
    await shadow.failed(await shadow.acquire(USERNAME, IP))
    loop = asyncio.get_running_loop()
    started = loop.time()
    result = await shadow.acquire(USERNAME, IP)
    assert isinstance(result, int)
    assert loop.time() - started < 0.4


async def test_postgres_blocked_memory_allowed_is_counted():
    postgres = FakePostgres(acquire="block")
    shadow, _ = make(postgres)
    reservation = await shadow.acquire(USERNAME, IP)
    assert isinstance(reservation, Reservation)  # memory's answer wins
    await shadow.failed(reservation)
    assert shadow.counts["postgres_blocked_memory_allowed"] == 1
    assert postgres.finished == []  # no Postgres reservation to finish


async def test_memory_blocked_postgres_allowed_is_counted_and_released():
    postgres = FakePostgres(acquire="allow")
    shadow, _ = make(postgres, max_failures=1)
    await shadow.failed(await shadow.acquire(USERNAME, IP))
    postgres.finished.clear()
    result = await shadow.acquire(USERNAME, IP)
    assert isinstance(result, int)  # memory blocks
    assert shadow.counts["memory_blocked_postgres_allowed"] == 1
    # Postgres had reserved an attempt that will not run: handed back.
    assert [outcome for outcome, _ in postgres.finished] == [RELEASED]


async def test_slow_postgres_calls_are_counted():
    shadow, _ = make(FakePostgres(delay=0.15), slow_seconds=0.05, wait_seconds=1.0)
    await shadow.failed(await shadow.acquire(USERNAME, IP))
    assert shadow.counts["slow"] == 1


async def test_disabled_shadow_skips_postgres():
    postgres = FakePostgres()
    shadow, _ = make(postgres)
    shadow.enabled = False
    assert postgres.enabled is False
    reservation = await shadow.acquire(USERNAME, IP)
    await shadow.failed(reservation)
    assert shadow.counts["calls"] == 0
    assert postgres.finished == []


async def test_summary_is_logged_at_most_once_a_minute_without_personal_data(caplog):
    postgres = FakePostgres(acquire="block")
    shadow, clock = make(postgres, log_interval_seconds=60.0)
    with caplog.at_level(logging.WARNING, logger="security.login_throttle_shadow"):
        await shadow.failed(await shadow.acquire(USERNAME, IP))
        assert caplog.records == []  # less than a minute since start
        clock.now += 61
        await shadow.failed(await shadow.acquire(USERNAME, IP))
        await shadow.failed(await shadow.acquire(USERNAME, IP))
        clock.now += 30
        await shadow.failed(await shadow.acquire(USERNAME, IP))
    (record,) = caplog.records
    message = record.getMessage()
    assert message.startswith("Login throttle shadow (last 61 s): ")
    assert "calls=2" in message
    assert "postgres_blocked_memory_allowed=2" in message
    for name in ("postgres_errors=0", "postgres_timeouts=0", "shadow_wait_exceeded=0", "memory_blocked_postgres_allowed=0", "slow=0"):
        assert name in message
    assert USERNAME not in message
    assert IP not in message
    assert shadow.counts["calls"] == 4  # cumulative totals keep counting


async def test_clear_clears_both_stores():
    postgres = FakePostgres()
    shadow, _ = make(postgres, max_failures=1)
    await shadow.failed(await shadow.acquire(USERNAME, IP))
    shadow.clear()
    assert postgres.cleared
    assert isinstance(await shadow.acquire(USERNAME, IP), Reservation)
