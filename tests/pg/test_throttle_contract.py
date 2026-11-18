"""
One test per throttle guarantee, run against both backends with the same
injected clock: whatever the memory backend promises, Postgres must too.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from security.login_throttle import LoginThrottle, MemoryLoginThrottle, Reservation
from security.login_throttle_postgres import PostgresLoginThrottle
from tests.pg.conftest import stored_rows

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
IP = "198.51.100.1"


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


LIMITS = {
    "max_failures": 3,
    "window_seconds": 60.0,
    "max_failures_per_username": 10,
    "max_usernames_per_client": 1000,
    "max_failures_per_prefix": 6,
    "max_tracked": 10_000,
    "pressure_ratio": 0.1,
}


@pytest.fixture()
def make(backend_engine):
    """make(**limits) -> (backend, clock) for the backend under test."""

    def build(**overrides):
        clock = FakeClock()
        limits = {**LIMITS, **overrides}
        if backend_engine is None:
            return MemoryLoginThrottle(LoginThrottle(clock=clock, **limits)), clock
        backend = PostgresLoginThrottle(
            backend_engine,
            **limits,
            load_cache_seconds=0,  # read pressure and capacity on every call
            now=lambda: EPOCH + timedelta(seconds=clock.now),
            monotonic=clock,
        )
        return backend, clock

    return build


async def fail(backend, username="alice", ip=IP, times=1):
    for _ in range(times):
        reservation = await backend.acquire(username, ip)
        assert isinstance(reservation, Reservation), reservation
        await backend.failed(reservation)


def blocked(result) -> bool:
    return isinstance(result, int)


def slash64(i: int, site: str = "2001:db8:aa") -> str:
    return f"{site}:{i:x}::/64"


async def test_only_failures_count(make):
    backend, _ = make()
    for _ in range(10):
        await backend.succeeded(await backend.acquire("alice", IP))
    await fail(backend, times=3)
    assert blocked(await backend.acquire("alice", IP))


async def test_success_clears_the_client_key(make):
    backend, _ = make(max_failures=3)
    await fail(backend, times=2)
    await backend.succeeded(await backend.acquire("alice", IP))
    await fail(backend, times=3)  # the full budget again
    assert blocked(await backend.acquire("alice", IP))


async def test_success_withdraws_only_its_own_48_stamp(make):
    backend, clock = make(
        max_failures=10, max_failures_per_username=1000, max_failures_per_prefix=3
    )
    await fail(backend, ip=slash64(1))
    clock.now += 1
    await fail(backend, ip=slash64(2))
    clock.now += 1
    await backend.succeeded(await backend.acquire("alice", slash64(3)))
    clock.now += 1
    # The two earlier failures still count: one more fills the /48 cap.
    await fail(backend, ip=slash64(4))
    assert blocked(await backend.acquire("alice", slash64(5)))


async def test_release_withdraws_exactly_one_stamp(make):
    backend, _ = make(max_failures=3)
    await fail(backend, times=2)
    reservation = await backend.acquire("alice", IP)
    await backend.release(reservation)
    await fail(backend)  # the third failure is still allowed
    assert blocked(await backend.acquire("alice", IP))


async def test_username_cap_blocks_only_clients_that_failed(make):
    backend, _ = make(max_failures=10, max_failures_per_username=4)
    for i in range(4):
        await fail(backend, ip=f"10.0.0.{i}")
    assert blocked(await backend.acquire("alice", "10.0.0.1"))
    assert isinstance(await backend.acquire("alice", "10.9.9.9"), Reservation)


async def test_48_cap_blocks_a_fresh_64(make):
    backend, _ = make(max_failures=10, max_failures_per_username=1000, max_failures_per_prefix=6)
    for i in range(6):
        await fail(backend, ip=slash64(i))
    assert blocked(await backend.acquire("alice", slash64(500)))
    assert isinstance(await backend.acquire("alice", slash64(1, site="2001:db8:bb")), Reservation)


@pytest.mark.parametrize(("pressure_ratio", "capped"), [(0.0, True), (1.0, False)])
async def test_per_client_username_cap_only_under_pressure(make, pressure_ratio, capped):
    backend, _ = make(
        max_usernames_per_client=3, pressure_ratio=pressure_ratio, max_tracked=1000
    )
    for i in range(3):
        await fail(backend, username=f"user{i}")
    result = await backend.acquire("newcomer", IP)
    assert blocked(result) is capped
    if capped:
        assert result == 60  # the window, as an upper bound


async def test_retry_after_is_the_max_over_the_limits(make):
    backend, clock = make(max_failures=5, window_seconds=100.0, max_failures_per_username=6)
    for i in range(5):
        clock.now = 1000 + i * 10
        await fail(backend, ip="10.0.0.1")
    clock.now = 1045
    for j in range(3):
        await fail(backend, ip=f"10.0.1.{j}")
    clock.now = 1050
    wait = await backend.acquire("alice", "10.0.0.1")
    assert wait == 70  # not 50: the username cap outlasts the client's own limit
    clock.now = 1050 + wait
    assert isinstance(await backend.acquire("alice", "10.0.0.1"), Reservation)


async def test_full_table_fails_closed_with_oldest_expiry_wait(make):
    backend, clock = make(max_tracked=2)
    await fail(backend, username="a")
    clock.now += 10
    await fail(backend, username="b")
    result = await backend.acquire("new", IP)
    assert result == 50  # until "a" (the oldest key) expires
    # Known keys below their limit are still allowed.
    assert isinstance(await backend.acquire("a", IP), Reservation)


async def test_disabled_backend_records_nothing(make):
    backend, _ = make()
    backend.enabled = False
    reservation = await backend.acquire("Alice", IP)
    assert reservation == Reservation("alice", IP, None)
    await backend.failed(reservation)


# ---------------------------------------------------------------------------
# The 200-character key cap (Postgres only)
# ---------------------------------------------------------------------------


async def test_nfkc_expanded_name_never_reaches_the_database(pg_engine, monkeypatch):
    """authenticate_user caps the throttle key; the table never sees more."""
    from fastapi import HTTPException

    from api.v1 import auth

    class NoUserSession:
        async def scalar(self, _statement):
            return None

    backend = PostgresLoginThrottle(pg_engine, **LIMITS)
    monkeypatch.setattr(auth, "login_throttle", backend)
    with pytest.raises(HTTPException) as raised:
        await auth.authenticate_user(NoUserSession(), "\ufdfa" * 200, "pw", IP)
    assert raised.value.status_code == 401
    rows = await stored_rows(pg_engine)
    assert rows
    assert max(len(row.username) for row in rows) <= 200


async def test_check_constraint_rejects_a_201_character_username(pg_engine):
    with pytest.raises(IntegrityError):
        async with pg_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO public.login_throttle_key (kind, username, scope, stamps, last_at) "
                    "VALUES (2, :username, '', ARRAY[now()], now())"
                ),
                {"username": "x" * 201},
            )
