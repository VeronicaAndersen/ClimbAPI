"""
Integration tests for rate limiting on the auth endpoints.

The autouse ``reset_rate_limits`` fixture in conftest.py clears limiter and
throttle state before each test. Defaults: login/token share 10/minute per IP,
signup 5/minute, and 5 attempts per username in 15 minutes.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from main import app
from security.login_throttle import login_throttle
from security.rate_limit import TOO_MANY_REQUESTS_DETAIL, limiter, rate_limit_settings

BASE = "/api/v1"


async def signup(client: AsyncClient, username="ratelimited", password="secret123"):
    return await client.post(
        f"{BASE}/auth/signup",
        json={
            "username": username,
            "password": password,
            "firstname": "Rate",
            "lastname": "Limit",
        },
    )


async def login(client: AsyncClient, username="ratelimited", password="secret123"):
    return await client.post(
        f"{BASE}/auth/login",
        json={
            "username": username,
            "password": password,
        },
    )


@pytest.fixture(autouse=True)
def force_rate_limiting_on(monkeypatch):
    """These tests assert the limits, so run them even if RATE_LIMIT_ENABLED=false."""
    monkeypatch.setattr(limiter, "enabled", True)
    monkeypatch.setattr(login_throttle, "enabled", True)


def assert_429(resp):
    assert resp.status_code == 429
    assert resp.json() == {"detail": TOO_MANY_REQUESTS_DETAIL}
    assert int(resp.headers["Retry-After"]) >= 1


class TestLoginIpRateLimit:
    async def test_login_succeeds_within_limit(self, client):
        await signup(client)
        resp = await login(client)
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_login_returns_429_after_ip_limit(self, client):
        # Different usernames so only the per-IP limit can trigger.
        for i in range(10):
            resp = await login(client, username=f"nobody{i}")
            assert resp.status_code == 401
        assert_429(await login(client, username="nobody-final"))

    async def test_login_and_token_share_ip_budget(self, client):
        for i in range(10):
            resp = await login(client, username=f"nobody{i}")
            assert resp.status_code == 401
        resp = await client.post(
            f"{BASE}/auth/token",
            data={"username": "someone", "password": "whatever"},
        )
        assert_429(resp)

    async def test_ip_limit_is_per_client_address(self, client):
        for i in range(10):
            await login(client, username=f"nobody{i}")
        assert (await login(client, username="x")).status_code == 429

        # The `client` fixture has installed the DB override on the app.
        other_ip = ASGITransport(app=app, client=("203.0.113.7", 40000))
        async with AsyncClient(transport=other_ip, base_url="http://test") as other:
            assert (await login(other, username="x")).status_code == 401


class TestSignupRateLimit:
    async def test_signup_returns_429_after_limit(self, client):
        for i in range(5):
            resp = await signup(client, username=f"user{i}")
            assert resp.status_code == 201
        assert_429(await signup(client, username="user-final"))


class TestUsernameThrottle:
    async def test_username_locked_after_max_failures(self, client):
        await signup(client)
        for _ in range(rate_limit_settings.LOGIN_MAX_FAILURES):
            resp = await login(client, password="wrongpassword")
            assert resp.status_code == 401

        # Even the correct password is refused while the username is locked.
        resp = await login(client)
        assert_429(resp)
        assert (
            int(resp.headers["Retry-After"])
            <= rate_limit_settings.LOGIN_FAILURE_WINDOW_SECONDS
        )

    async def test_unknown_username_is_throttled_the_same_way(self, client):
        for _ in range(rate_limit_settings.LOGIN_MAX_FAILURES):
            assert (await login(client, username="ghost")).status_code == 401
        assert_429(await login(client, username="ghost"))

    async def test_lockout_is_per_username(self, client):
        await signup(client, username="victim")
        await signup(client, username="bystander")
        for _ in range(rate_limit_settings.LOGIN_MAX_FAILURES):
            await login(client, username="victim", password="wrongpassword")

        assert (await login(client, username="victim")).status_code == 429
        assert (await login(client, username="bystander")).status_code == 200

    async def test_lockout_ignores_username_case(self, client):
        await signup(client)
        for _ in range(rate_limit_settings.LOGIN_MAX_FAILURES):
            await login(client, username="RateLimited", password="wrongpassword")
        assert (await login(client, username="ratelimited")).status_code == 429

    async def test_successful_login_resets_failure_count(self, client):
        await signup(client)
        for _ in range(rate_limit_settings.LOGIN_MAX_FAILURES - 1):
            await login(client, password="wrongpassword")
        assert (await login(client)).status_code == 200

        for _ in range(rate_limit_settings.LOGIN_MAX_FAILURES - 1):
            assert (await login(client, password="wrongpassword")).status_code == 401
        assert (await login(client)).status_code == 200
