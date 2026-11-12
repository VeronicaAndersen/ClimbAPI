"""Database errors reach clients and logs without row data."""

import logging

from sqlalchemy.exc import IntegrityError

from db.config import get_session
from main import app

BASE = "/api/v1"
SECRET_USERNAME = "alice-secret-username"
DETAIL = f"DETAIL:  Key (username)=({SECRET_USERNAME}) already exists."


class UniqueViolation(Exception):
    """Stands in for asyncpg's driver error, which carries a sqlstate."""

    sqlstate = "23505"


class ExplodingSession:
    async def scalar(self, _statement):
        raise IntegrityError(
            "SELECT climber.id FROM climber WHERE climber.username = $1",
            {"username": SECRET_USERNAME},
            UniqueViolation(f'duplicate key value violates unique constraint\n{DETAIL}'),
        )


async def test_dbapi_error_is_generic_500_and_logs_no_row_data(client, caplog):
    """Regression: the error message, with asyncpg's DETAIL line, reached the logs."""

    async def exploding_session():
        yield ExplodingSession()

    app.dependency_overrides[get_session] = exploding_session
    with caplog.at_level(logging.DEBUG):
        resp = await client.post(
            f"{BASE}/auth/login", json={"username": SECRET_USERNAME, "password": "pw"}
        )

    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error"}
    assert SECRET_USERNAME not in resp.text
    logged = "\n".join(
        record.getMessage() + (str(record.exc_info[1]) if record.exc_info else "")
        for record in caplog.records
    )
    assert SECRET_USERNAME not in logged
    assert "DETAIL" not in logged
    assert "UniqueViolation" in logged
    assert "23505" in logged


class RacingSignupSession:
    """The username check passes, then the insert hits the unique constraint."""

    async def scalar(self, _statement):
        return None

    def add(self, _obj):
        pass

    async def flush(self):
        raise IntegrityError("INSERT INTO climber ...", {}, UniqueViolation(DETAIL))

    async def rollback(self):
        pass


async def test_router_integrity_error_handling_still_returns_409(client):
    """Signup catches IntegrityError itself; the global handler must not take over."""

    async def racing_session():
        yield RacingSignupSession()

    app.dependency_overrides[get_session] = racing_session
    resp = await client.post(
        f"{BASE}/auth/signup",
        json={"username": "dupe", "password": "secret123", "firstname": "A", "lastname": "B"},
    )
    assert resp.status_code == 409
    assert resp.json() == {"detail": "Username is already taken"}
