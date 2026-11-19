"""
Shared per-IP limit for the login routes, kept in Postgres
(LOGIN_IP_LIMIT_BACKEND=postgres).

Vercel's free plan may not offer WAF rate-limit rules, and the in-memory
limiter counts per instance. This counts per client key across all
instances, with the same rules as the in-memory login limit: fixed windows
that start at the first hit, every limit in RATE_LIMIT_LOGIN checked first and
a hit counted in all of them only when none is exceeded, 429 + Retry-After.
It runs after the in-memory check, as a second, shared layer.

Storage: the login_ip_limit table and login_ip_limit_hit() function from
db/migrations/20261118000000_add_login_throttle.sql, one row per (client key,
period). One round trip per login request, in autocommit.

If the database is slow or down the request is refused with 429 and
FAIL_CLOSED_RETRY_AFTER_SECONDS: a limit that silently stops counting would
fail open.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from limits import RateLimitItem
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from security.rate_limited_warning import RateLimitedWarning

logger = logging.getLogger(__name__)

# Retry-After when a shared limit or throttle cannot be reached. Long enough
# not to hammer a struggling database, short enough that a brief outage (a
# Neon cold start, a failover) does not lock users out for long.
FAIL_CLOSED_RETRY_AFTER_SECONDS = 10

_HIT = text(
    "SELECT retry_after FROM public.login_ip_limit_hit("
    "CAST(:client AS text), CAST(:amounts AS integer[]), CAST(:periods AS integer[]), "
    "CAST(:lock_timeout_ms AS integer), CAST(:sweep_batch AS integer), "
    "CAST(:now AS timestamptz))"
)


def require_asyncpg_engine(engine: AsyncEngine, setting: str) -> None:
    """Fail startup unless the app's database is Postgres through asyncpg."""
    if engine.url.drivername != "postgresql+asyncpg":
        raise RuntimeError(
            f"{setting} needs a Postgres database through asyncpg "
            "(DATABASE_URL or ASYNC_DATABASE_URL pointing at Postgres)."
        )


async def run_bounded(
    engine: AsyncEngine,
    statement: Any,
    params: dict[str, Any],
    *,
    deadline: float,
) -> list[Any]:
    """
    Run one statement on its own connection, until the loop-time `deadline`.

    If the limit (or a cancellation of the request) interrupts the query, the
    connection is invalidated before it is released: the driver may be left
    mid-protocol, and such a connection must never go back into the pool for
    the next request. Shared by the IP limit and the login throttle.
    """
    connection = None
    try:
        async with asyncio.timeout_at(deadline):
            connection = await engine.connect()
            result = await connection.execute(statement, params)
            return list(result.all())
    except (TimeoutError, asyncio.CancelledError):
        if connection is not None:
            await connection.invalidate()
        raise
    finally:
        if connection is not None:
            await connection.close()


def lock_timeout_ms(db_timeout_seconds: float) -> int:
    """
    Advisory-lock wait inside the database: half the client-side timeout, so
    a lock wait fails cleanly in Postgres before the query is cancelled.
    """
    return max(1, int(db_timeout_seconds * 500))


class PostgresIpLimit:
    def __init__(
        self,
        engine: AsyncEngine,
        items: tuple[RateLimitItem, ...],
        *,
        db_timeout_seconds: float,
        sweep_batch: int = 200,
        now: Callable[[], datetime | None] = lambda: None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine.execution_options(isolation_level="AUTOCOMMIT")
        self._amounts = [item.amount for item in items]
        self._periods = [int(item.get_expiry()) for item in items]
        self._db_timeout = db_timeout_seconds
        self._lock_timeout_ms = lock_timeout_ms(db_timeout_seconds)
        self._sweep_batch = sweep_batch
        self._now = now
        self._warn_unavailable = RateLimitedWarning(
            logger,
            "Shared login IP limit unavailable (database error or timeout); "
            "refusing logins with 429",
            clock=monotonic,
        )

    async def hit(self, client_key: str) -> int | None:
        """Count one login request; the seconds to wait if it is over a limit."""
        params = {
            "client": client_key,
            "amounts": self._amounts,
            "periods": self._periods,
            "lock_timeout_ms": self._lock_timeout_ms,
            "sweep_batch": self._sweep_batch,
            "now": self._now(),
        }
        try:
            deadline = asyncio.get_running_loop().time() + self._db_timeout
            rows = await run_bounded(self._engine, _HIT, params, deadline=deadline)
            if len(rows) != 1:
                raise SQLAlchemyError("login_ip_limit_hit returned an unexpected row count")
            return rows[0][0]
        except (TimeoutError, SQLAlchemyError, OSError) as error:
            # Type only: no client key, no SQL text.
            logger.debug("Login IP limit query failed (%s)", type(error).__name__)
            self._warn_unavailable()
            return FAIL_CLOSED_RETRY_AFTER_SECONDS
