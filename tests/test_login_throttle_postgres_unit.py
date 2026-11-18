"""
PostgresLoginThrottle and the shared login IP limit without a database:
failure modes, settings and wiring. Behavior against a real Postgres is in
tests/pg/.
"""

import logging

import pytest
from limits import parse_many
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from security.login_ip_limit_postgres import (
    FAIL_CLOSED_RETRY_AFTER_SECONDS,
    PostgresIpLimit,
)
from security.login_throttle import Reservation
from security.login_throttle_postgres import PostgresLoginThrottle

# Nothing listens on port 1, so every connection attempt fails at once.
UNREACHABLE = "postgresql+asyncpg://nobody@127.0.0.1:1/none"
USERNAME = "secret-user-name"
CLIENT = "203.0.113.77"


def throttle(engine, **kwargs) -> PostgresLoginThrottle:
    options = {
        "max_failures": 3,
        "window_seconds": 60,
        "max_failures_per_username": 10,
        "max_usernames_per_client": 20,
        "max_failures_per_prefix": 6,
        "db_timeout_seconds": 2,
    }
    options.update(kwargs)
    return PostgresLoginThrottle(engine, **options)


@pytest.fixture()
async def unreachable_engine():
    engine = create_async_engine(UNREACHABLE)
    yield engine
    await engine.dispose()


async def test_acquire_fails_closed_with_429_when_database_is_down(unreachable_engine, caplog):
    backend = throttle(unreachable_engine)
    with caplog.at_level(logging.WARNING):
        result = await backend.acquire(USERNAME, CLIENT)
    assert result == FAIL_CLOSED_RETRY_AFTER_SECONDS
    assert "refusing logins with 429" in caplog.text
    assert USERNAME not in caplog.text
    assert CLIENT not in caplog.text


async def test_finish_errors_are_logged_and_swallowed(unreachable_engine, caplog):
    from datetime import UTC, datetime

    backend = throttle(unreachable_engine)
    reservation = Reservation(USERNAME, CLIENT, datetime(2026, 1, 1, tzinfo=UTC))
    with caplog.at_level(logging.WARNING):
        await backend.failed(reservation)
        await backend.succeeded(reservation)
        await backend.release(reservation)
    assert "could not record a login outcome" in caplog.text
    assert USERNAME not in caplog.text


async def test_failed_load_query_means_pressure_on_and_not_full(unreachable_engine):
    backend = throttle(unreachable_engine, load_cache_seconds=60)
    assert await backend._current_load() is None
    assert backend._under_pressure(None) is True
    assert backend._full_kinds(None) == []


def test_load_counts_decide_pressure_and_capacity(unreachable_engine):
    backend = throttle(unreachable_engine, max_tracked=100, pressure_ratio=0.1)
    assert backend._under_pressure({1: 9, 2: 0, 3: 0}) is False
    assert backend._under_pressure({1: 0, 2: 10, 3: 0}) is True
    assert backend._full_kinds({1: 100, 2: 99, 3: 100}) == [1, 3]


async def test_disabled_backend_never_touches_the_database(unreachable_engine):
    backend = throttle(unreachable_engine, enabled=False)
    reservation = await backend.acquire("Alice", CLIENT)
    assert reservation == Reservation("alice", CLIENT, None)
    await backend.failed(reservation)


async def test_a_memory_reservation_is_rejected(unreachable_engine):
    backend = throttle(unreachable_engine)
    with pytest.raises(TypeError):
        await backend.failed(Reservation("alice", CLIENT, 1000.0))


async def test_ip_limit_fails_closed_when_database_is_down(unreachable_engine, caplog):
    limit = PostgresIpLimit(unreachable_engine, tuple(parse_many("10/minute")), db_timeout_seconds=2)
    with caplog.at_level(logging.WARNING):
        assert await limit.hit(CLIENT) == FAIL_CLOSED_RETRY_AFTER_SECONDS
    assert "Shared login IP limit unavailable" in caplog.text
    assert CLIENT not in caplog.text


# ---------------------------------------------------------------------------
# Settings and wiring
# ---------------------------------------------------------------------------


def test_ip_limit_backend_setting(monkeypatch):
    from security.rate_limit import load_rate_limit_settings

    monkeypatch.delenv("LOGIN_IP_LIMIT_BACKEND", raising=False)
    assert load_rate_limit_settings().LOGIN_IP_LIMIT_BACKEND == "memory"
    monkeypatch.setenv("LOGIN_IP_LIMIT_BACKEND", "postgres")
    assert load_rate_limit_settings().LOGIN_IP_LIMIT_BACKEND == "postgres"
    monkeypatch.setenv("LOGIN_IP_LIMIT_BACKEND", "redis")
    with pytest.raises(ValidationError):
        load_rate_limit_settings()


def test_shared_ip_limit_off_by_default():
    from security.rate_limit import RateLimitSettings, _shared_login_ip_limit

    assert _shared_login_ip_limit(RateLimitSettings()) is None


def test_shared_ip_limit_needs_an_asyncpg_database(monkeypatch):
    from db import config
    from security.rate_limit import RateLimitSettings, _shared_login_ip_limit

    monkeypatch.setattr(config, "async_engine", create_async_engine("sqlite+aiosqlite://"))
    with pytest.raises(RuntimeError, match="LOGIN_IP_LIMIT_BACKEND=postgres"):
        _shared_login_ip_limit(RateLimitSettings(LOGIN_IP_LIMIT_BACKEND="postgres"))


async def test_route_limit_runs_shared_check_only_after_memory_passes():
    from starlette.requests import Request

    from security.rate_limit import RateLimiter, RouteLimit

    seen: list[str] = []

    async def shared(key: str) -> int | None:
        seen.append(key)
        return 7 if len(seen) >= 2 else None

    limiter = RateLimiter(enabled=True)
    route_limit = RouteLimit(limiter, tuple(parse_many("3/minute")), "test-scope", shared)
    request = Request({"type": "http", "headers": [], "client": (CLIENT, 1)})

    assert await route_limit.check(request) is None  # memory 1/3, shared allows
    assert await route_limit.check(request) == 7  # memory 2/3, shared refuses
    assert await route_limit.check(request) == 7  # memory 3/3, shared refuses
    blocked = await route_limit.check(request)  # memory refuses first
    assert blocked is not None and blocked != 7
    assert seen == [CLIENT, CLIENT, CLIENT]

    limiter.enabled = False
    assert await route_limit.check(request) is None
    assert len(seen) == 3  # a disabled limiter skips the shared check too
