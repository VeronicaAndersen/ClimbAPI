"""
The real get_session dependency, not the conftest override, with a real
SQLite AsyncSession: every request's session is closed, whatever happens.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db import config
from db.config import get_session
from main import app

BASE = "/api/v1"
SIGNUP = {"username": "realsession", "password": "secret123", "firstname": "R", "lastname": "S"}


@pytest.fixture()
def tracked_sessions(engine, monkeypatch):
    """Route the real get_session to the test engine and record each session."""
    sessions: list[AsyncSession] = []

    class TrackingSession(AsyncSession):
        closed = False

        async def close(self) -> None:
            await super().close()
            self.closed = True

    factory = async_sessionmaker(bind=engine, class_=TrackingSession, expire_on_commit=False)

    def make_session() -> TrackingSession:
        session = factory()
        sessions.append(session)
        return session

    monkeypatch.setattr(config, "AsyncSessionLocal", make_session)
    assert get_session not in app.dependency_overrides
    return sessions


@pytest.fixture()
async def real_client(tracked_sessions):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_session_closed_after_normal_request(real_client, tracked_sessions):
    resp = await real_client.post(f"{BASE}/auth/signup", json=SIGNUP)
    assert resp.status_code == 201
    assert len(tracked_sessions) == 1
    assert tracked_sessions[0].closed


async def test_session_closed_after_http_error_request(real_client, tracked_sessions):
    assert (await real_client.post(f"{BASE}/auth/signup", json=SIGNUP)).status_code == 201
    resp = await real_client.post(f"{BASE}/auth/signup", json=SIGNUP)  # duplicate
    assert resp.status_code == 409
    assert len(tracked_sessions) == 2
    assert all(session.closed for session in tracked_sessions)


async def test_session_closed_after_database_error_request(engine, real_client, tracked_sessions):
    async with engine.begin() as conn:
        await conn.exec_driver_sql("DROP TABLE climber")
    resp = await real_client.post(
        f"{BASE}/auth/login", json={"username": "someone", "password": "secret123"}
    )
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error"}
    assert len(tracked_sessions) == 1
    assert tracked_sessions[0].closed
