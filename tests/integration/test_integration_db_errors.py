"""Database errors reach clients and logs without row data."""

import logging

from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from db.config import get_session
from main import app

BASE = "/api/v1"
SECRET_USERNAME = "alice-secret-username"
DETAIL = f"DETAIL:  Key (username)=({SECRET_USERNAME}) already exists."


class UniqueViolation(Exception):
    """Stands in for asyncpg's driver error, which carries a sqlstate."""

    sqlstate = "23505"


class UvicornLikeErrorLogging:
    """
    Logs exceptions that escape the app with their traceback, as uvicorn does
    ("Exception in ASGI application"). The test harness alone logs nothing for
    them, so without this a leak through the server log would go unnoticed.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        try:
            await self.app(scope, receive, send)
        except Exception:
            logging.getLogger("uvicorn.error").exception("Exception in ASGI application")


def _all_logged_text(records) -> str:
    """Messages plus formatted tracebacks, i.e. what a log handler would write."""
    formatter = logging.Formatter()
    parts = []
    for record in records:
        parts.append(record.getMessage())
        if record.exc_info:
            parts.append(formatter.formatException(record.exc_info))
    return "\n".join(parts)


STATEMENT = "SELECT climber.id FROM climber WHERE climber.username = $1"


HIDDEN_PARAMETERS_MARKER = "[SQL parameters hidden due to hide_parameters=True]"


def _production_like_error() -> IntegrityError:
    """As the app's engine raises it: parameters hidden, DETAIL still present."""
    return IntegrityError(
        STATEMENT,
        {"username": SECRET_USERNAME},
        UniqueViolation(f"duplicate key value violates unique constraint\n{DETAIL}"),
        hide_parameters=True,
    )


class ExplodingSession:
    async def scalar(self, _statement):
        raise _production_like_error()


async def test_dbapi_error_is_generic_500_and_logs_no_row_data(client, caplog):
    """
    Regression: the error escaped to the server, which logged its message and
    traceback, including asyncpg's DETAIL line with the username.
    """

    async def exploding_session():
        yield ExplodingSession()

    # `client` is requested for its cleanup of app.dependency_overrides.
    app.dependency_overrides[get_session] = exploding_session
    transport = ASGITransport(app=UvicornLikeErrorLogging(app))
    with caplog.at_level(logging.DEBUG):
        async with AsyncClient(transport=transport, base_url="http://test") as server:
            resp = await server.post(
                f"{BASE}/auth/login", json={"username": SECRET_USERNAME, "password": "pw"}
            )

    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error"}
    assert SECRET_USERNAME not in resp.text
    logged = _all_logged_text(caplog.records)
    assert SECRET_USERNAME not in logged
    assert "DETAIL" not in logged
    assert "UniqueViolation" in logged
    assert "23505" in logged
    # The statement is logged for debugging, with placeholders only.
    assert STATEMENT in logged
    # The error's own text is not logged at all, not even in redacted form:
    # it would still carry the DETAIL line.
    assert HIDDEN_PARAMETERS_MARKER not in logged
    assert SECRET_USERNAME not in logged


def test_production_like_error_hides_parameters_but_keeps_detail():
    """Guards the setup above: hide_parameters alone does not hide DETAIL."""
    text = str(_production_like_error())
    assert HIDDEN_PARAMETERS_MARKER in text
    assert f"'username': '{SECRET_USERNAME}'" not in text
    assert DETAIL in text


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


async def test_logged_statement_is_capped(caplog):
    from types import SimpleNamespace

    from main import database_error_handler

    long_statement = "SELECT " + ", ".join(f"col{i}" for i in range(2000)) + " FROM t"
    exc = IntegrityError(long_statement, {}, UniqueViolation("boom"))
    request = SimpleNamespace(method="GET", url=SimpleNamespace(path="/x"))
    with caplog.at_level(logging.ERROR):
        resp = await database_error_handler(request, exc)
    assert resp.status_code == 500
    message = caplog.records[-1].getMessage()
    assert message.endswith(" ...")
    assert len(message) < 1200
