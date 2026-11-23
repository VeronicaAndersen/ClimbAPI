"""Shadow mode against a real Postgres: memory decides, both stores record."""

from datetime import UTC, datetime, timedelta

from security.login_throttle import LoginThrottle, MemoryLoginThrottle, Reservation
from security.login_throttle_postgres import PostgresLoginThrottle
from security.login_throttle_shadow import ShadowLoginThrottle
from tests.pg.conftest import stored_rows

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
IP = "198.51.100.1"


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def build(engine, **limits):
    clock = FakeClock()
    limits = {
        "max_failures": 3,
        "window_seconds": 60.0,
        "max_failures_per_username": 100,
        "max_usernames_per_client": 100,
        "max_failures_per_prefix": 100,
        **limits,
    }
    memory = MemoryLoginThrottle(LoginThrottle(clock=clock, **limits))
    postgres = PostgresLoginThrottle(
        engine,
        **limits,
        load_cache_seconds=0,
        now=lambda: EPOCH + timedelta(seconds=clock.now),
    )
    return ShadowLoginThrottle(memory, postgres, wait_seconds=2.0), memory, clock


async def test_shadow_answers_like_memory_and_both_stores_record(pg_engine):
    shadow, memory, _ = build(pg_engine)
    results = []
    for _ in range(4):
        result = await shadow.acquire("alice", IP)
        results.append(result if isinstance(result, int) else "allowed")
        if isinstance(result, Reservation):
            await shadow.failed(result)
    assert results[:3] == ["allowed"] * 3
    assert isinstance(results[3], int)
    # Memory recorded the three failures...
    assert len(memory.throttle._by_client.recent(("alice", IP))) == 3
    # ...and so did Postgres, for the client key and the username.
    (client_row,) = await stored_rows(pg_engine, kind=1)
    (username_row,) = await stored_rows(pg_engine, kind=2)
    assert len(client_row.stamps) == 3
    assert len(username_row.stamps) == 3
    # They agreed on every decision.
    assert shadow.counts["memory_blocked_postgres_allowed"] == 0
    assert shadow.counts["postgres_blocked_memory_allowed"] == 0
    assert shadow.counts["postgres_errors"] == 0
    assert shadow.counts["calls"] == 4


async def test_success_and_release_reach_both_stores(pg_engine):
    shadow, memory, _ = build(pg_engine)
    await shadow.failed(await shadow.acquire("alice", IP))
    released = await shadow.acquire("alice", IP)
    await shadow.release(released)
    (client_row,) = await stored_rows(pg_engine, kind=1)
    assert len(client_row.stamps) == 1  # only the failure remains in Postgres
    await shadow.succeeded(await shadow.acquire("alice", IP))
    assert await stored_rows(pg_engine, kind=1) == []
    assert memory.throttle._by_client.recent(("alice", IP)) is None


async def test_a_disagreement_is_counted_but_memory_answers(pg_engine):
    shadow, _, _ = build(pg_engine, max_failures=3)
    # Postgres alone already has three failures for alice (e.g. from
    # another instance); memory has none.
    for _ in range(3):
        await shadow.postgres.failed(await shadow.postgres.acquire("alice", IP))
    result = await shadow.acquire("alice", IP)
    assert isinstance(result, Reservation)  # memory allows, so the login runs
    await shadow.failed(result)
    assert shadow.counts["postgres_blocked_memory_allowed"] == 1
