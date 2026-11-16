"""POST /api/v1/climber is admin only, and checks that before anything else."""

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker

from db.models import Climber, UserScope
from security.hashing import hash_password

BASE = "/api/v1"
PASSWORD = "secret123"


def climber_body(username: str) -> dict:
    return {"username": username, "password": PASSWORD, "firstname": "N", "lastname": "C"}


async def create_account(engine, username: str, scope=UserScope.climber) -> None:
    async with async_sessionmaker(engine)() as session:
        session.add(
            Climber(
                username=username,
                password=hash_password(PASSWORD),
                firstname="F",
                lastname="L",
                user_scope=scope,
            )
        )
        await session.commit()


async def bearer(client, username: str) -> dict:
    resp = await client.post(
        f"{BASE}/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def statements(engine):
    seen: list[str] = []

    def record(_conn, _cursor, statement, _params, _context, _executemany):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    yield seen
    event.remove(engine.sync_engine, "before_cursor_execute", record)


def username_lookups(statements: list[str]) -> list[str]:
    """Statements that filter on a username or create a climber."""
    markers = ("climber.username = ", "climber.username IN", "INSERT INTO climber")
    return [s for s in statements if any(marker in s for marker in markers)]


class TestCreateClimberRequiresAdmin:
    @pytest.mark.parametrize("username", ["existing", "brand-new", "anna \U0001f338"])
    async def test_unauthenticated_gets_401_without_any_query(
        self, client, engine, statements, username
    ):
        """Regression: the endpoint was open, a free username-enumeration oracle."""
        await create_account(engine, "existing")
        statements.clear()
        resp = await client.post(f"{BASE}/climber", json=climber_body(username))
        assert resp.status_code == 401
        assert statements == []

    @pytest.mark.parametrize("username", ["existing", "brand-new", "anna \U0001f338"])
    async def test_non_admin_gets_403_before_any_username_check(
        self, client, engine, statements, username
    ):
        await create_account(engine, "existing")
        await create_account(engine, "member")
        headers = await bearer(client, "member")
        statements.clear()
        resp = await client.post(
            f"{BASE}/climber", json=climber_body(username), headers=headers
        )
        assert resp.status_code == 403
        assert resp.json() == {"detail": "Insufficient permissions"}
        # Only the caller's own account was loaded (by id), no username lookup.
        assert statements
        assert all("WHERE climber.id = " in s for s in statements)
        assert username_lookups(statements) == []


class TestAdminCreatesClimber:
    async def test_admin_creates_climber(self, client, engine):
        await create_account(engine, "boss", UserScope.admin)
        headers = await bearer(client, "boss")
        resp = await client.post(
            f"{BASE}/climber", json=climber_body("\uff2eew Climber"), headers=headers
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["username"] == "new climber"
        assert "password" not in resp.json()

    async def test_admin_gets_409_for_taken_username(self, client, engine):
        await create_account(engine, "boss", UserScope.admin)
        await create_account(engine, "admin")
        headers = await bearer(client, "boss")
        resp = await client.post(
            f"{BASE}/climber", json=climber_body("\uff41dmin"), headers=headers
        )
        assert resp.status_code == 409
        assert resp.json() == {"detail": "Username is already taken"}

    async def test_admin_gets_422_for_invalid_username(self, client, engine):
        await create_account(engine, "boss", UserScope.admin)
        headers = await bearer(client, "boss")
        resp = await client.post(
            f"{BASE}/climber", json=climber_body("anna \U0001f338"), headers=headers
        )
        assert resp.status_code == 422
        (error,) = resp.json()["detail"]
        assert error["loc"] == ["body", "username"]
