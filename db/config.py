from collections.abc import Awaitable
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from typing import AsyncIterator
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

async def _cleanup_after_error(
    step: Awaitable[None], name: str, original: Exception
) -> None:
    """
    Run a rollback or close after a request failed, without masking the error.

    If cleanup raised, its exception would chain to the original one, and the
    chained traceback can print the original's driver message (row data such
    as a username). So cleanup errors are logged by type only and dropped; the
    original exception keeps propagating to the app's error handlers.
    """
    try:
        await step
    # Deliberately broad: any cleanup error must be dropped, not chained.
    except Exception as cleanup_error:  # noqa: BLE001
        logger.error(
            "%s failed after %s (%s)",
            name,
            type(original).__name__,
            type(cleanup_error).__name__,
        )


async def get_session() -> AsyncIterator[AsyncSession]:
    session = AsyncSessionLocal()
    failure: Exception | None = None
    try:
        yield session
    except Exception as exc:
        failure = exc
        await _cleanup_after_error(session.rollback(), "Rollback", exc)
        raise
    finally:
        if failure is None:
            await session.close()
        else:
            await _cleanup_after_error(session.close(), "Close", failure)
