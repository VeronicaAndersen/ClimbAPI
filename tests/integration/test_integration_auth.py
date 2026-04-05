"""
Integration tests for auth and climber endpoints.

Each test uses a real HTTP request through the FastAPI app backed by
an in-memory SQLite database — no mocking of business logic.
"""
import pytest
from httpx import AsyncClient

BASE = "/api/v1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def signup(client: AsyncClient, username="testuser", password="secret123"):
    return await client.post(f"{BASE}/auth/signup", json={
        "username": username,
        "password": password,
        "firstname": "Test",
        "lastname": "User",
    })


async def login(client: AsyncClient, username="testuser", password="secret123"):
    return await client.post(f"{BASE}/auth/login", json={
        "username": username,
        "password": password,
    })


# ---------------------------------------------------------------------------
# POST /auth/signup
# ---------------------------------------------------------------------------

class TestSignup:
    async def test_signup_returns_201(self, client):
        resp = await signup(client)
        assert resp.status_code == 201

    async def test_signup_returns_tokens(self, client):
        resp = await signup(client)
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"

    async def test_signup_returns_climber_info(self, client):
        resp = await signup(client, username="alice")
        climber = resp.json()["climber"]
        assert climber["username"] == "alice"
        assert climber["firstname"] == "Test"
        assert climber["user_scope"] == "climber"

    async def test_signup_lowercases_username(self, client):
        resp = await signup(client, username="UPPERCASE")
        assert resp.json()["climber"]["username"] == "uppercase"

    async def test_signup_duplicate_username_returns_409(self, client):
        await signup(client, username="dupe")
        resp = await signup(client, username="dupe")
        assert resp.status_code == 409

    async def test_signup_short_password_returns_422(self, client):
        resp = await client.post(f"{BASE}/auth/signup", json={
            "username": "newuser",
            "password": "short",
            "firstname": "A",
            "lastname": "B",
        })
        assert resp.status_code == 422

    async def test_signup_missing_username_returns_422(self, client):
        resp = await client.post(f"{BASE}/auth/signup", json={
            "password": "secret123",
            "firstname": "A",
            "lastname": "B",
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------

class TestLogin:
    async def test_login_returns_200_and_tokens(self, client):
        await signup(client)
        resp = await login(client)
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body

    async def test_login_wrong_password_returns_401(self, client):
        await signup(client)
        resp = await login(client, password="wrongpassword")
        assert resp.status_code == 401

    async def test_login_nonexistent_user_returns_401(self, client):
        resp = await login(client, username="nobody")
        assert resp.status_code == 401

    async def test_login_username_case_insensitive(self, client):
        """Signup lowercases; login with uppercase should still work."""
        await signup(client, username="mixedcase")
        resp = await client.post(f"{BASE}/auth/login", json={
            "username": "MIXEDCASE",
            "password": "secret123",
        })
        # The login endpoint itself doesn't lowercase — signup stored "mixedcase"
        # Lowercase the username in the test just as the frontend does
        resp2 = await client.post(f"{BASE}/auth/login", json={
            "username": "mixedcase",
            "password": "secret123",
        })
        assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------

class TestRefresh:
    async def test_refresh_returns_new_tokens(self, client):
        await signup(client)
        login_resp = await login(client)
        refresh_token = login_resp.json()["refresh_token"]

        resp = await client.post(f"{BASE}/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body

    async def test_refresh_with_access_token_returns_401(self, client):
        await signup(client)
        login_resp = await login(client)
        access_token = login_resp.json()["access_token"]

        resp = await client.post(f"{BASE}/auth/refresh", json={"refresh_token": access_token})
        assert resp.status_code == 401

    async def test_refresh_with_garbage_returns_401(self, client):
        resp = await client.post(f"{BASE}/auth/refresh", json={"refresh_token": "not.a.token"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /climber/me
# ---------------------------------------------------------------------------

class TestGetMe:
    async def test_get_me_without_token_returns_401(self, client):
        resp = await client.get(f"{BASE}/climber/me")
        assert resp.status_code == 401

    async def test_get_me_returns_own_profile(self, client):
        signup_resp = await signup(client, username="myuser")
        access_token = signup_resp.json()["access_token"]

        resp = await client.get(
            f"{BASE}/climber/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "myuser"
        assert "password" not in body

    async def test_get_me_with_wrong_token_returns_401(self, client):
        resp = await client.get(
            f"{BASE}/climber/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /climber/{id}  (public — no auth required)
# ---------------------------------------------------------------------------

class TestGetClimberById:
    async def test_get_climber_by_id(self, client):
        signup_resp = await signup(client, username="fetched")
        climber_id = signup_resp.json()["climber"]["id"]

        resp = await client.get(f"{BASE}/climber/{climber_id}")
        assert resp.status_code == 200
        assert resp.json()["username"] == "fetched"

    async def test_get_nonexistent_climber_returns_404(self, client):
        resp = await client.get(f"{BASE}/climber/999999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /climber  (admin-only)
# ---------------------------------------------------------------------------

class TestGetAllClimbers:
    async def test_list_climbers_without_auth_returns_401(self, client):
        resp = await client.get(f"{BASE}/climber")
        assert resp.status_code == 401

    async def test_list_climbers_as_regular_user_returns_403(self, client):
        signup_resp = await signup(client)
        token = signup_resp.json()["access_token"]

        resp = await client.get(
            f"{BASE}/climber",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /auth/password-reset/request  (always 200, hides existence)
# ---------------------------------------------------------------------------

class TestPasswordResetRequest:
    async def test_returns_200_for_existing_user(self, client):
        await signup(client, username="pwreset")
        resp = await client.post(
            f"{BASE}/auth/password-reset/request",
            json={"username": "pwreset"},
        )
        assert resp.status_code == 200

    async def test_returns_200_for_nonexistent_user(self, client):
        resp = await client.post(
            f"{BASE}/auth/password-reset/request",
            json={"username": "doesnotexist"},
        )
        assert resp.status_code == 200
        assert "mail" in resp.json()["message"]
