"""
Postgres tests for the shared login throttle and login IP limit.

The Postgres tests run only when TEST_POSTGRES_URL points at a throwaway
database with the migrations applied; otherwise only they are skipped and
the memory side of the contract tests still runs. For example:

    createdb -h localhost climbapi_throttle_test
    DATABASE_URL='postgres://<user>@localhost/climbapi_throttle_test?sslmode=disable' \
        dbmate --migrations-dir db/migrations --no-dump-schema up
    TEST_POSTGRES_URL='postgresql+asyncpg://<user>@localhost/climbapi_throttle_test' \
        python -m pytest tests/pg

Never point it at a real database: every test truncates the throttle tables.
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

TEST_POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")

_REQUIRED = (
    "to_regclass('public.login_throttle_key') IS NOT NULL",
    "to_regclass('public.login_ip_limit') IS NOT NULL",
    "to_regproc('public.login_throttle_acquire') IS NOT NULL",
    "to_regproc('public.login_throttle_finish') IS NOT NULL",
    "to_regproc('public.login_throttle_load') IS NOT NULL",
    "to_regproc('public.login_ip_limit_hit') IS NOT NULL",
)


def _require_test_postgres() -> None:
    """Skip only what needs Postgres; memory-side contract tests always run."""
    if not TEST_POSTGRES_URL:
        pytest.skip("set TEST_POSTGRES_URL to run the Postgres throttle tests")


async def open_pg_engine() -> AsyncEngine:
    """A fresh engine with room for 30+ concurrent connections, tables emptied."""
    _require_test_postgres()
    engine = create_async_engine(TEST_POSTGRES_URL, pool_size=40, max_overflow=10)
    async with engine.begin() as connection:
        missing = [
            check
            for check in _REQUIRED
            if not (await connection.execute(text(f"SELECT {check}"))).scalar_one()
        ]
        if missing:
            await engine.dispose()
            pytest.fail(
                "TEST_POSTGRES_URL lacks the login throttle migration; run dbmate up "
                f"against it first (missing: {', '.join(missing)})"
            )
        await connection.execute(text("TRUNCATE public.login_throttle_key, public.login_ip_limit"))
    return engine


@pytest.fixture()
async def pg_engine() -> AsyncEngine:
    engine = await open_pg_engine()
    yield engine
    await engine.dispose()


@pytest.fixture(params=["memory", "postgres", "shadow"])
async def backend_engine(request):
    """
    (kind, engine): no engine for the memory backend, a clean Postgres
    engine for postgres and shadow (memory decides, Postgres alongside).
    """
    if request.param == "memory":
        yield request.param, None
        return
    engine = await open_pg_engine()
    yield request.param, engine
    await engine.dispose()


async def stored_rows(engine: AsyncEngine, kind: int | None = None) -> list:
    """Rows of login_throttle_key, oldest key first."""
    query = "SELECT kind, username, scope, stamps, last_at FROM public.login_throttle_key"
    if kind is not None:
        query += f" WHERE kind = {int(kind)}"
    query += " ORDER BY kind, username, scope"
    async with engine.connect() as connection:
        return list((await connection.execute(text(query))).all())
