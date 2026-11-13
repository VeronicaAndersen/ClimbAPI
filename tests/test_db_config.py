"""Unit tests for db/config.py"""

import asyncio
import logging
import traceback
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from db import config
from db.config import async_engine


def test_engine_hides_bound_parameters():
    """Regression: SQL errors used to include bound values such as usernames."""
    assert async_engine.sync_engine.hide_parameters is True


async def test_sql_error_message_does_not_contain_parameters():
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", hide_parameters=True)
    try:
        async with engine.connect() as conn:
            try:
                await conn.execute(
                    text("SELECT * FROM missing_table WHERE name = :name"),
                    {"name": "attacker-controlled-value"},
                )
            except DBAPIError as exc:
                message = str(exc)
            else:  # pragma: no cover - the table does not exist
                raise AssertionError("expected an error")
    finally:
        await engine.dispose()
    assert "attacker-controlled-value" not in message
    assert "hidden due to hide_parameters" in message


# ---------------------------------------------------------------------------
# get_session cleanup after a failed request
# ---------------------------------------------------------------------------

SECRET = "alice-secret-username"


class _Orig(Exception):
    sqlstate = "23505"


def _integrity_error():
    return IntegrityError(
        "INSERT INTO climber ...",
        {"username": SECRET},
        _Orig(f"DETAIL:  Key (username)=({SECRET}) already exists."),
    )


class _BrokenSession:
    def __init__(self, *, rollback_error=None, close_error=None):
        self.rollback_error = rollback_error
        self.close_error = close_error
        self.closed = False

    async def rollback(self):
        if self.rollback_error is not None:
            raise self.rollback_error

    async def close(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


async def _run_failing_request(monkeypatch, session, error):
    monkeypatch.setattr(config, "AsyncSessionLocal", lambda: session)
    gen = config.get_session()
    assert await gen.__anext__() is session
    return await gen.athrow(error)


async def test_rollback_failure_reraises_original_and_logs_only_types(monkeypatch, caplog):
    """
    Regression: a failing rollback replaced the DBAPIError, and the chained
    traceback logged the original DETAIL text with the username.
    """
    error = _integrity_error()
    session = _BrokenSession(rollback_error=RuntimeError(f"rollback broke for {SECRET}"))
    with caplog.at_level(logging.DEBUG), pytest.raises(type(error)) as raised:
        await _run_failing_request(monkeypatch, session, error)

    assert raised.value is error
    assert session.closed
    messages = [r.getMessage() for r in caplog.records]
    assert messages == ["Rollback failed after IntegrityError (RuntimeError)"]
    assert all(r.exc_info is None for r in caplog.records)


async def test_close_failure_reraises_original_and_logs_only_types(monkeypatch, caplog):
    error = _integrity_error()
    session = _BrokenSession(close_error=OSError(f"close broke for {SECRET}"))
    with caplog.at_level(logging.DEBUG), pytest.raises(type(error)) as raised:
        await _run_failing_request(monkeypatch, session, error)

    assert raised.value is error
    assert [r.getMessage() for r in caplog.records] == [
        "Close failed after IntegrityError (OSError)"
    ]


async def test_session_is_closed_after_success(monkeypatch):
    session = _BrokenSession()
    monkeypatch.setattr(config, "AsyncSessionLocal", lambda: session)
    gen = config.get_session()
    await gen.__anext__()
    try:
        await gen.__anext__()
    except StopAsyncIteration:
        pass
    assert session.closed


# ---------------------------------------------------------------------------
# Cancellation: the session is still closed, and nothing chains the DB error
# ---------------------------------------------------------------------------


class _SlowSession:
    """Rollback and close take a moment, like real I/O, and can be observed."""

    def __init__(self):
        self.rollback_started = asyncio.Event()
        self.rolled_back = False
        self.closed = False

    async def rollback(self):
        self.rollback_started.set()
        await asyncio.sleep(0.02)
        self.rolled_back = True

    async def close(self):
        await asyncio.sleep(0.02)
        self.closed = True


def _text_of(exc: BaseException) -> str:
    """The traceback a logger would print for ``exc``, chains included."""
    return "".join(traceback.format_exception(exc))


async def _cancelled_result(task: asyncio.Task) -> asyncio.CancelledError:
    with pytest.raises(asyncio.CancelledError) as raised:
        await task
    return raised.value


async def test_cancel_during_endpoint_still_closes_session(monkeypatch, caplog):
    """Regression: a cancelled request abandoned close() and leaked its connection."""
    session = _SlowSession()
    monkeypatch.setattr(config, "AsyncSessionLocal", lambda: session)
    in_endpoint = asyncio.Event()

    async def request():
        async with asynccontextmanager(config.get_session)():
            in_endpoint.set()
            await asyncio.sleep(10)

    task = asyncio.create_task(request())
    await in_endpoint.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()  # a second cancel arrives during close()
    error = await _cancelled_result(task)

    assert session.closed
    assert SECRET not in _text_of(error)
    assert SECRET not in caplog.text


async def test_cancel_during_rollback_still_closes_and_hides_db_error(monkeypatch, caplog):
    """
    Regression: a cancel during rollback abandoned close(), and the
    CancelledError chained the original DBAPIError, printing its DETAIL.
    """
    session = _SlowSession()
    monkeypatch.setattr(config, "AsyncSessionLocal", lambda: session)

    async def request():
        async with asynccontextmanager(config.get_session)():
            raise _integrity_error()

    task = asyncio.create_task(request())
    await session.rollback_started.wait()
    task.cancel()
    with caplog.at_level(logging.DEBUG):
        error = await _cancelled_result(task)

    assert session.rolled_back
    assert session.closed
    assert error.__suppress_context__
    assert SECRET not in _text_of(error)
    assert "DETAIL" not in _text_of(error)
    assert SECRET not in caplog.text


async def test_aclose_closes_session(monkeypatch):
    session = _SlowSession()
    monkeypatch.setattr(config, "AsyncSessionLocal", lambda: session)
    gen = config.get_session()
    assert await gen.__anext__() is session
    await gen.aclose()  # GeneratorExit inside the generator
    assert session.closed
    assert not session.rolled_back


async def test_cancel_during_normal_close_finishes_close(monkeypatch):
    session = _SlowSession()
    monkeypatch.setattr(config, "AsyncSessionLocal", lambda: session)
    body_done = asyncio.Event()

    async def request():
        async with asynccontextmanager(config.get_session)():
            body_done.set()

    task = asyncio.create_task(request())
    await body_done.wait()
    await asyncio.sleep(0)  # now inside close()
    task.cancel()
    await _cancelled_result(task)
    assert session.closed


async def test_failed_commit_is_reraised_unchanged(monkeypatch):
    session = _SlowSession()
    monkeypatch.setattr(config, "AsyncSessionLocal", lambda: session)
    error = _integrity_error()

    async def request():
        async with asynccontextmanager(config.get_session)():
            raise error

    with pytest.raises(type(error)) as raised:
        await request()
    assert raised.value is error
    assert session.rolled_back
    assert session.closed
