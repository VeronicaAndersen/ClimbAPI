"""Unit tests for db/config.py"""

import logging

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
