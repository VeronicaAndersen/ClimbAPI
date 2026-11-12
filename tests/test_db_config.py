"""Unit tests for db/config.py"""

from sqlalchemy import text

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
