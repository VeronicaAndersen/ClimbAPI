import asyncio
from collections.abc import Coroutine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from typing import Any, AsyncIterator
import logging
import os

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("ASYNC_DATABASE_URL") or "sqlite+aiosqlite:///./greppapi.db"
if not os.getenv("ASYNC_DATABASE_URL"):
    os.environ["ASYNC_DATABASE_URL"] = DATABASE_URL

async_engine = create_async_engine(
    DATABASE_URL,
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

async def _run_to_completion(
    step: Coroutine[Any, Any, None],
) -> tuple[BaseException | None, asyncio.CancelledError | None]:
    """
    Run ``step`` in its own task and wait for it even if we are cancelled.

    Modelled on SQLAlchemy's private helper of the same name, which
    ``AsyncSession.__aexit__`` uses so that close() is never abandoned and the
    connection always goes back to the pool. Unlike that helper this one does
    not raise: it returns the step's own error and any cancellation received
    meanwhile, so the caller decides what to raise and how to chain it.
    """
    task = asyncio.ensure_future(step)
    cancelled: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as err:
            if not task.done():
                cancelled = err  # we were cancelled; keep waiting for the step
        except BaseException:  # noqa: BLE001 - the step's own error, read below
            break
    if task.cancelled():
        return asyncio.CancelledError(), cancelled
    return task.exception(), cancelled


async def _close_after_failure(
    session: AsyncSession, original: BaseException
) -> asyncio.CancelledError | None:
    """
    Roll back (after an error) and close, each run to completion.

    Cleanup errors are logged by type only and dropped: chained to the
    original they would print its driver message (row data such as a
    username) in a traceback. Returns a cancellation received meanwhile.
    """
    steps = [("Close", session.close)]
    if isinstance(original, Exception):
        steps.insert(0, ("Rollback", session.rollback))
    cancelled: asyncio.CancelledError | None = None
    for name, step in steps:
        error, step_cancelled = await _run_to_completion(step())
        cancelled = cancelled or step_cancelled
        if error is not None:
            logger.error(
                "%s failed after %s (%s)",
                name,
                type(original).__name__,
                type(error).__name__,
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
            raise cancelled from None
        if isinstance(exc, asyncio.CancelledError):
            raise exc from None
        raise
    else:
        error, cancelled = await _run_to_completion(session.close())
        if error is not None:
            raise error
        if cancelled is not None:
            raise cancelled
