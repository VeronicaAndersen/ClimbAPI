"""authenticate_user and the login throttle when the credential check errors."""

import asyncio

import pytest

from api.v1.auth import authenticate_user
from security.login_throttle import login_throttle

IP = "198.51.100.1"


class FailingSession:
    """Stands in for AsyncSession; the lookup raises like a lost DB connection."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    async def scalar(self, _statement):
        raise self.exc


@pytest.fixture(autouse=True)
def throttle_on(monkeypatch):
    monkeypatch.setattr(login_throttle, "enabled", True)
    login_throttle.clear()
    yield
    login_throttle.clear()


async def test_server_error_does_not_count_as_failed_login():
    """Regression: DB errors used to leave the reservation as a failure."""
    for _ in range(login_throttle.max_failures + 2):
        with pytest.raises(RuntimeError):
            await authenticate_user(
                FailingSession(RuntimeError("db down")), "alice", "pw", IP
            )
    assert login_throttle.acquire("alice", IP) is None
    assert len(login_throttle._by_username) == 0


async def test_cancelled_attempt_stays_counted():
    with pytest.raises(asyncio.CancelledError):
        await authenticate_user(
            FailingSession(asyncio.CancelledError()), "alice", "pw", IP
        )
    assert len(login_throttle._by_client.recent(("alice", IP))) == 1
