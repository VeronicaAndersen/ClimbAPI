"""
Postgres-only guarantees: concurrency across connections (as across
instances), lock timeouts, query counts, sweeping, and the shared login IP
limit.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from limits import parse_many
from sqlalchemy import event, text

from security.login_ip_limit_postgres import (
    FAIL_CLOSED_RETRY_AFTER_SECONDS,
    PostgresIpLimit,
)
from security.login_throttle import Reservation
from security.login_throttle_postgres import PostgresLoginThrottle
from tests.pg.conftest import stored_rows

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
IP = "198.51.100.1"


def throttle(engine, clock_seconds=None, **overrides) -> PostgresLoginThrottle:
    limits = {
        "max_failures": 5,
        "window_seconds": 60.0,
        "max_failures_per_username": 1000,
        "max_usernames_per_client": 1000,
        "max_failures_per_prefix": 1000,
        "max_tracked": 10_000,
        "pressure_ratio": 0.1,
        "load_cache_seconds": 0,
    }
    limits.update(overrides)
    now = (lambda: EPOCH + timedelta(seconds=clock_seconds())) if clock_seconds else (lambda: None)
    return PostgresLoginThrottle(engine, now=now, **limits)


def reservations(results) -> list[Reservation]:
    return [r for r in results if isinstance(r, Reservation)]


# ---------------------------------------------------------------------------
# 30 concurrent acquires, each on its own connection
# ---------------------------------------------------------------------------


async def test_concurrent_acquires_reserve_exactly_the_client_limit(pg_engine):
    backend = throttle(pg_engine, max_failures=5)
    results = await asyncio.gather(*(backend.acquire("alice", IP) for _ in range(30)))
    assert len(reservations(results)) == 5
    assert all(isinstance(r, int) and r >= 1 for r in results if not isinstance(r, Reservation))
    (row,) = await stored_rows(pg_engine, kind=1)
    assert len(row.stamps) == 5
    assert len(set(row.stamps)) == 5  # every reservation has its own id


async def test_concurrent_acquires_reserve_exactly_the_48_limit(pg_engine):
    backend = throttle(pg_engine, max_failures=100, max_failures_per_prefix=6)
    clients = [f"2001:db8:aa:{i:x}::/64" for i in range(30)]
    results = await asyncio.gather(*(backend.acquire("alice", c) for c in clients))
    assert len(reservations(results)) == 6
    (row,) = await stored_rows(pg_engine, kind=3)
    assert len(row.stamps) == 6


async def test_concurrent_acquires_reserve_exactly_the_per_client_cap_under_pressure(pg_engine):
    backend = throttle(pg_engine, max_usernames_per_client=4, pressure_ratio=0.0)
    results = await asyncio.gather(*(backend.acquire(f"user{i}", IP) for i in range(30)))
    assert len(reservations(results)) == 4
    assert len(await stored_rows(pg_engine, kind=1)) == 4


async def test_concurrent_release_and_acquire_leave_consistent_counts(pg_engine):
    backend = throttle(pg_engine, max_failures=10)
    held = [await backend.acquire("alice", IP) for _ in range(4)]
    assert all(isinstance(r, Reservation) for r in held)
    results = await asyncio.gather(
        backend.release(held[0]),
        backend.release(held[1]),
        backend.acquire("alice", IP),
        backend.acquire("alice", IP),
        backend.acquire("alice", IP),
    )
    new = reservations(results)
    assert len(new) == 3
    (row,) = await stored_rows(pg_engine, kind=1)
    expected = {held[2].at, held[3].at, *(r.at for r in new)}
    assert set(row.stamps) == expected
    assert len(row.stamps) == 5
    assert row.last_at == max(row.stamps)


async def test_release_waits_for_the_reservation_lock(pg_engine):
    """
    Regression: withdrawing took no lock, so a reservation could land
    between its UPDATE and DELETE and the withdrawn stamp stayed counted.
    """
    backend = throttle(pg_engine, db_timeout_seconds=2.0)
    held = await backend.acquire("alice", IP)
    loop = asyncio.get_running_loop()
    async with pg_engine.connect() as holder:
        transaction = await holder.begin()
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(1, hashtext(:key))"), {"key": f"alice|{IP}"}
        )
        release = asyncio.create_task(backend.release(held))
        await asyncio.sleep(0.3)
        assert not release.done()  # waits for the key's lock like acquire does
        await transaction.rollback()
    started = loop.time()
    await release
    assert loop.time() - started < 0.5
    assert await stored_rows(pg_engine, kind=1) == []


async def test_concurrent_acquire_and_withdraw_leave_exact_stamps(pg_engine):
    backend = throttle(pg_engine, max_failures=1000, max_failures_per_prefix=1000)
    client = "2001:db8:dd:1::/64"
    kept: list[Reservation] = []
    for _ in range(10):
        first = await backend.acquire("alice", client)
        second = await backend.acquire("alice", client)
        results = await asyncio.gather(
            backend.release(first),
            backend.acquire("alice", client),
            backend.release(second),
            backend.acquire("alice", client),
        )
        kept.extend(reservations(results))
    expected = {r.at for r in kept}
    (client_row,) = await stored_rows(pg_engine, kind=1)
    (site_row,) = await stored_rows(pg_engine, kind=3)
    assert set(client_row.stamps) == expected and len(client_row.stamps) == len(expected)
    assert set(site_row.stamps) == expected and len(site_row.stamps) == len(expected)
    assert client_row.last_at == max(client_row.stamps)


async def test_held_advisory_lock_times_out_and_fails_closed(pg_engine):
    backend = throttle(pg_engine, db_timeout_seconds=1.0)
    async with pg_engine.connect() as holder:
        transaction = await holder.begin()
        # The first lock an acquire for (alice, IP) takes.
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(1, hashtext(:key))"), {"key": f"alice|{IP}"}
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await backend.acquire("alice", IP)
        elapsed = loop.time() - started
        await transaction.rollback()
    assert result == FAIL_CLOSED_RETRY_AFTER_SECONDS
    assert elapsed < 1.5
    assert await stored_rows(pg_engine) == []
    # The lock is gone: the next attempt works.
    assert isinstance(await backend.acquire("alice", IP), Reservation)


async def test_stuck_login_on_one_client_does_not_delay_another_client(pg_engine):
    """
    Regression: every acquire locked the whole username, so a flood on one
    name from many addresses queued the real user's login past
    lock_timeout and got it refused with 429.
    """
    backend = throttle(pg_engine, db_timeout_seconds=2.0)
    other = "203.0.113.99"
    loop = asyncio.get_running_loop()
    async with pg_engine.connect() as holder:
        transaction = await holder.begin()
        # Holding the other client's lock leaves an acquire for (alice,
        # other) stuck after it has taken its first lock.
        await holder.execute(text("SELECT pg_advisory_xact_lock(2, hashtext(:c))"), {"c": other})
        stuck = asyncio.create_task(backend.acquire("alice", other))
        await asyncio.sleep(0.3)
        assert not stuck.done()
        started = loop.time()
        result = await backend.acquire("alice", IP)
        elapsed = loop.time() - started
        await transaction.rollback()
    assert isinstance(await stuck, Reservation)
    assert isinstance(result, Reservation)
    assert elapsed < 0.5


async def test_two_64s_in_one_48_still_serialize_on_the_48(pg_engine):
    """Same /48 lock: the /48 count stays exact for concurrent /64s."""
    backend = throttle(pg_engine, max_failures=100, max_failures_per_prefix=3)
    clients = [f"2001:db8:cc:{i:x}::/64" for i in range(2) for _ in range(10)]
    results = await asyncio.gather(*(backend.acquire("alice", c) for c in clients))
    assert len(reservations(results)) == 3


async def test_slow_load_means_pressure_on_without_429_and_is_cached(pg_engine, monkeypatch):
    """
    Regression: the load shared acquire's time limit, so a slow load refused
    the login with 429, and was not cached, so every login did it again.
    """
    from security import login_throttle_postgres as module

    # Test hook: a load reading that takes 2 s (all parameters still bound).
    monkeypatch.setattr(
        module,
        "_LOAD",
        text(
            "SELECT 1::smallint AS key_kind, 0 AS active_keys FROM pg_sleep(2) "
            "WHERE coalesce(CAST(:now AS timestamptz), now()) IS NOT NULL "
            "AND CAST(:window AS interval) IS NOT NULL AND CAST(:limit AS integer) > 0"
        ),
    )
    # With these limits a real reading would NOT be pressure, so the
    # per-client cap applying below shows the throttle assumed pressure.
    backend = throttle(
        pg_engine,
        db_timeout_seconds=1.5,
        load_cache_seconds=60,
        max_usernames_per_client=1,
        pressure_ratio=1.0,
    )
    sleeps: list[str] = []

    def record(_conn, _cursor, statement, *_args):
        if "pg_sleep" in statement:
            sleeps.append(statement)

    event.listen(pg_engine.sync_engine, "before_cursor_execute", record)
    try:
        loop = asyncio.get_running_loop()
        started = loop.time()
        first = await backend.acquire("alice", IP)
        elapsed = loop.time() - started
        assert isinstance(first, Reservation)  # not the fail-closed 429
        assert elapsed < 1.0  # gave up on the load after 0.5 s
        await backend.failed(first)
        assert backend._load is None
        # Pressure on: a second username from the same client is capped.
        assert await backend.acquire("bob", IP) == 60
        assert await backend.acquire("carol", IP) == 60
    finally:
        event.remove(pg_engine.sync_engine, "before_cursor_execute", record)
    assert len(sleeps) == 1  # cached for the TTL, not re-run per login
    # The connection cut off by the timeout was not reused: queries still work.
    assert len(await stored_rows(pg_engine, kind=1)) == 1


# ---------------------------------------------------------------------------
# Query counts do not reveal whether a username exists
# ---------------------------------------------------------------------------


async def test_statement_count_is_the_same_for_known_and_unknown_usernames(pg_engine, monkeypatch):
    from fastapi import HTTPException

    from api.v1 import auth
    from db.models import Climber
    from security.hashing import hash_password

    known = Climber(id=1, username="known", password=hash_password("right-password"))

    class Session:
        async def scalar(self, statement):
            params = statement.compile().params
            return known if "known" in params.values() else None

    backend = throttle(pg_engine, load_cache_seconds=3600)
    monkeypatch.setattr(auth, "login_throttle", backend)
    statements: list[str] = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(pg_engine.sync_engine, "before_cursor_execute", record)
    try:
        await backend._current_load()  # fill the load cache first
        counts = []
        for username in ("known", "unknown"):
            statements.clear()
            with pytest.raises(HTTPException) as raised:
                await auth.authenticate_user(Session(), username, "wrong-password", IP)
            assert raised.value.status_code == 401
            counts.append(len(statements))
    finally:
        event.remove(pg_engine.sync_engine, "before_cursor_execute", record)
    assert counts[0] == counts[1] == 2  # one acquire, one finish


# ---------------------------------------------------------------------------
# Sweeping expired keys
# ---------------------------------------------------------------------------


async def test_expired_keys_are_swept_on_write(pg_engine):
    clock = {"now": 0.0}
    backend = throttle(pg_engine, clock_seconds=lambda: clock["now"])
    for i in range(5):
        await backend.failed(await backend.acquire(f"old{i}", IP))
    assert len(await stored_rows(pg_engine)) == 10  # 5 client keys, 5 username keys
    clock["now"] = 61.0  # all of them have expired
    await backend.acquire("fresh", IP)
    rows = await stored_rows(pg_engine)
    assert [(row.kind, row.username) for row in rows] == [(1, "fresh")]


async def test_sweep_is_bounded_per_write(pg_engine):
    clock = {"now": 0.0}
    backend = throttle(pg_engine, clock_seconds=lambda: clock["now"])
    for i in range(20):
        await backend.acquire(f"old{i}", IP)
    clock["now"] = 61.0
    await backend.acquire("fresh", IP)
    assert len(await stored_rows(pg_engine)) == 20 - 16 + 1


# ---------------------------------------------------------------------------
# Shared login IP limit
# ---------------------------------------------------------------------------


def ip_limit(engine, value="3/minute", clock_seconds=None) -> PostgresIpLimit:
    now = (lambda: EPOCH + timedelta(seconds=clock_seconds())) if clock_seconds else (lambda: None)
    return PostgresIpLimit(engine, tuple(parse_many(value)), db_timeout_seconds=3, now=now)


async def test_ip_limit_is_shared_between_instances(pg_engine):
    first, second = ip_limit(pg_engine), ip_limit(pg_engine)
    assert [await first.hit(IP), await second.hit(IP), await first.hit(IP)] == [None] * 3
    assert isinstance(await second.hit(IP), int)
    assert await second.hit("203.0.113.5") is None  # other clients have their own budget


async def test_ip_limit_concurrent_hits_count_exactly(pg_engine):
    limit = ip_limit(pg_engine, "10/minute")
    results = await asyncio.gather(*(limit.hit(IP) for _ in range(30)))
    assert results.count(None) == 10


async def test_ip_limit_window_resets_and_reports_retry_after(pg_engine):
    clock = {"now": 0.0}
    limit = ip_limit(pg_engine, "2/minute", clock_seconds=lambda: clock["now"])
    assert await limit.hit(IP) is None
    clock["now"] = 15.0
    assert await limit.hit(IP) is None
    assert await limit.hit(IP) == 45  # until the window that began at 0 ends
    clock["now"] = 60.0
    assert await limit.hit(IP) is None


async def test_ip_limit_checks_every_limit_before_counting(pg_engine):
    limit = ip_limit(pg_engine, "2/minute;3/hour")
    assert [await limit.hit(IP) for _ in range(2)] == [None, None]
    assert isinstance(await limit.hit(IP), int)  # per minute exceeded
    async with pg_engine.connect() as connection:
        hourly = (
            await connection.execute(
                text("SELECT hits FROM public.login_ip_limit WHERE period_seconds = 3600")
            )
        ).scalar_one()
    assert hourly == 2  # the refused request was not counted in the hourly limit



# ---------------------------------------------------------------------------
# Function hardening
# ---------------------------------------------------------------------------


async def test_plpgsql_functions_pin_search_path_and_sql_helpers_stay_inlinable(pg_engine):
    async with pg_engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT p.proname, l.lanname, p.proconfig FROM pg_proc p "
                    "JOIN pg_language l ON l.oid = p.prolang "
                    "WHERE p.proname LIKE 'login\\_%' ORDER BY p.proname"
                )
            )
        ).all()
    plpgsql = {row.proname: row.proconfig for row in rows if row.lanname == "plpgsql"}
    sql = {row.proname: row.proconfig for row in rows if row.lanname == "sql"}
    assert set(plpgsql) == {
        "login_ip_limit_hit",
        "login_throttle_acquire",
        "login_throttle_finish",
        "login_throttle_withdraw",
    }
    assert all(config == ["search_path=pg_catalog, public"] for config in plpgsql.values())
    # A SET clause would stop the planner from inlining these small helpers.
    assert sql and all(config is None for config in sql.values())
