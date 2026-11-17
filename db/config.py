import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from typing import Any, AsyncIterator
from uuid import uuid4
import logging
import os

import anyio

logger = logging.getLogger(__name__)


def _cleanup_timeout_from_env() -> float:
    raw = os.getenv("DB_CLEANUP_TIMEOUT_SECONDS", "10")
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not value > 0:
        raise RuntimeError("DB_CLEANUP_TIMEOUT_SECONDS must be a positive number of seconds.")
    return value


# Upper bound for each rollback or close after a request; a hung database must
# not hold the request (and its worker slot) forever.
CLEANUP_TIMEOUT_SECONDS = _cleanup_timeout_from_env()

SQLITE_FALLBACK_URL = "sqlite+aiosqlite:///./greppapi.db"


def asyncpg_url_from_libpq(raw: str) -> URL:
    """
    Convert a libpq-style URL (such as Neon's DATABASE_URL) for asyncpg.

    asyncpg takes ``ssl`` instead of ``sslmode`` and rejects
    ``channel_binding`` as an unknown keyword argument.
    """
    url = make_url(raw)
    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode is not None:
        query["ssl"] = sslmode
    return url.set(drivername="postgresql+asyncpg", query=query)


def database_url_from_env() -> str | URL:
    """
    ASYNC_DATABASE_URL wins; otherwise DATABASE_URL (for example the pooled
    Neon URL written by `neon env pull`) is used. Postgres URLs from either
    variable are converted for asyncpg; otherwise a local SQLite file is used.
    """
    explicit = os.getenv("ASYNC_DATABASE_URL")
    if explicit:
        # A Neon URL pasted here still carries libpq params asyncpg rejects.
        if make_url(explicit).get_backend_name() == "postgresql":
            return asyncpg_url_from_libpq(explicit)
        return explicit
    libpq = os.getenv("DATABASE_URL")
    if libpq:
        return asyncpg_url_from_libpq(libpq)
    if os.getenv("VERCEL"):
        # Serverless has a read-only, ephemeral filesystem and no aiosqlite;
        # a silent SQLite fallback would only fail later with a confusing error.
        raise RuntimeError(
            "No database configured: set DATABASE_URL or ASYNC_DATABASE_URL "
            "in the Vercel project's environment variables."
        )
    return SQLITE_FALLBACK_URL


def connect_args_for(url: str | URL) -> dict[str, Any]:
    """
    Neon's pooled endpoint (``-pooler`` host) is PgBouncer in transaction
    mode: the next statement may run on another server connection, so
    asyncpg must not cache prepared statements and must name them uniquely.
    """
    host = make_url(url).host or ""
    if "-pooler." not in host:
        return {}
    return {
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
    }


DATABASE_URL = database_url_from_env()

async_engine = create_async_engine(
    DATABASE_URL,
    connect_args=connect_args_for(DATABASE_URL),
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=1800,
    # Keep bound values (usernames, tokens, other user input) out of
    # exception messages and therefore out of error logs.
    hide_parameters=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    expire_on_commit=False,
    class_=AsyncSession
)

@dataclass(frozen=True)
class _StepOutcome:
    error: BaseException | None  # the step's own error, if it finished
    cancelled: asyncio.CancelledError | None  # our caller's cancellation
    timed_out: bool  # the step was still running at the deadline


# Cleanup tasks still running, e.g. after a timeout. The event loop keeps only
# weak references to tasks, so without this a hung close() could be collected
# mid-flight.
_RUNNING_CLEANUP: set[asyncio.Task[None]] = set()


async def _run_to_completion(
    step: Coroutine[Any, Any, None], timeout: float | None = None
) -> _StepOutcome:
    """
    Run ``step`` in its own task and wait for it even if we are cancelled.

    Modelled on SQLAlchemy's private helper of the same name, which
    ``AsyncSession.__aexit__`` uses so that close() is never abandoned and the
    connection always goes back to the pool. Unlike that helper this one does
    not raise; the caller decides what to raise and how to chain it. It also
    gives up after ``timeout`` seconds (leaving the task running).

    The wait sits in a shielded anyio cancel scope: anyio re-delivers a scope
    cancellation on every loop turn, which would otherwise make this loop
    spin. A native task.cancel() still interrupts the wait and is recorded.
    """
    task: asyncio.Task[None] = asyncio.ensure_future(step)
    _RUNNING_CLEANUP.add(task)
    task.add_done_callback(_RUNNING_CLEANUP.discard)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (CLEANUP_TIMEOUT_SECONDS if timeout is None else timeout)
    cancelled: asyncio.CancelledError | None = None
    with anyio.CancelScope(shield=True):
        while not task.done():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return _StepOutcome(None, cancelled, timed_out=True)
            try:
                # wait() never cancels the task it waits for, and does not
                # raise the task's own error.
                await asyncio.wait((task,), timeout=remaining)
            except asyncio.CancelledError as err:
                # Our caller was cancelled. Recorded even if the step finished
                # in the same loop turn: dropping it would let the request
                # carry on as if never cancelled. Keep waiting for the step.
                cancelled = err
    if task.cancelled():
        return _StepOutcome(asyncio.CancelledError(), cancelled, timed_out=False)
    return _StepOutcome(task.exception(), cancelled, timed_out=False)


async def _invalidate(session: AsyncSession) -> None:
    """
    After a timed-out cleanup step, make sure the connection is not reused.

    invalidate() discards the session's connections instead of returning them
    to the pool. Bounded by the same timeout; failures are logged by type.
    """
    invalidate = getattr(session, "invalidate", None)
    if invalidate is None:
        return
    outcome = await _run_to_completion(invalidate())
    if outcome.timed_out or outcome.error is not None:
        logger.error(
            "Invalidate failed after a cleanup timeout (%s)",
            "timeout" if outcome.timed_out else type(outcome.error).__name__,
        )


async def _close_after_failure(
    session: AsyncSession, original: BaseException
) -> asyncio.CancelledError | None:
    """
    Roll back (after an error) and close, each run to completion.

    Cleanup errors are logged by type only and dropped: chained to the
    original they would print its driver message (row data such as a
    username) in a traceback. A step that times out is logged, the session
    is invalidated and the remaining steps are skipped. Returns a
    cancellation received meanwhile.
    """
    steps = [("Close", session.close)]
    if isinstance(original, Exception):
        steps.insert(0, ("Rollback", session.rollback))
    cancelled: asyncio.CancelledError | None = None
    for name, step in steps:
        outcome = await _run_to_completion(step())
        cancelled = cancelled or outcome.cancelled
        if outcome.timed_out:
            logger.error("%s timed out after %s", name, type(original).__name__)
            await _invalidate(session)
            break
        if outcome.error is not None:
            logger.error(
                "%s failed after %s (%s)",
                name,
                type(original).__name__,
                type(outcome.error).__name__,
            )
    return cancelled


async def get_session() -> AsyncIterator[AsyncSession]:
    """
    One session per request, always closed so its connection returns to the
    pool, even when the request is cancelled or the generator is closed.

    The original exception is re-raised unchanged, so a failed commit is never
    hidden. A cancellation is re-raised without chaining (``from None``) so
    no traceback prints a database error's driver message through it.
    """
    session = AsyncSessionLocal()
    try:
        yield session
    except BaseException as exc:
        cancelled = await _close_after_failure(session, exc)
        if cancelled is not None and cancelled is not exc:
            if isinstance(exc, Exception):
                # The cancellation replaces the error, which no handler will
                # see now; record that it happened, by type only.
                logger.error("Request cancelled during cleanup after %s", type(exc).__name__)
            raise cancelled from None
        if isinstance(exc, asyncio.CancelledError):
            raise exc from None
        raise
    else:
        outcome = await _run_to_completion(session.close())
        if outcome.timed_out:
            logger.error("Close timed out after a successful request")
            await _invalidate(session)
        if outcome.cancelled is not None:
            # The cancellation wins; a close error would only hide it.
            if outcome.error is not None:
                logger.error(
                    "Close failed during cancellation (%s)", type(outcome.error).__name__
                )
            raise outcome.cancelled
        if outcome.error is not None:
            raise outcome.error
