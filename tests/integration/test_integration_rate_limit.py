"""
Integration tests for rate limiting on the auth endpoints.

The autouse ``reset_rate_limits`` fixture in conftest.py clears limiter and
throttle state before each test. Defaults: login/token share 10/minute per IP,
signup 5/minute, and 5 failed logins per (username, client IP) in 15 minutes.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from limits import parse_many

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


    async def test_ipv6_clients_in_same_64_share_budget(self, client):
        """Regression: rotating addresses inside a /64 bypassed the IP limit."""
        for i in range(10):
            transport = ASGITransport(app=app, client=(f"2001:db8:0:1::{i + 1:x}", 40000))
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                assert (await login(c, username=f"nobody{i}")).status_code == 401
        transport = ASGITransport(app=app, client=("2001:db8:0:1::ffff", 40000))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            assert_429(await login(c, username="x"))

    async def test_username_throttle_groups_ipv6_by_64(self, client):
        await signup(client)
        for i in range(rate_limit_settings.LOGIN_MAX_FAILURES):
            transport = ASGITransport(app=app, client=(f"2001:db8:0:2::{i + 1:x}", 40000))
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                assert (await login(c, password="wrongpassword")).status_code == 401
        transport = ASGITransport(app=app, client=("2001:db8:0:2::ffff", 40000))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            assert_429(await login(c))


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

        # Even the correct password is refused from this IP while it is locked,
        # otherwise 200 vs 429 would reveal a correct guess.
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

    async def test_attacker_cannot_lock_out_user_on_another_ip(self, client):
        """Regression: failures from one IP used to lock the username everywhere."""
        await signup(client)
        attacker = ASGITransport(app=app, client=("203.0.113.66", 40000))
        async with AsyncClient(transport=attacker, base_url="http://test") as evil:
            for _ in range(rate_limit_settings.LOGIN_MAX_FAILURES):
                assert (await login(evil, password="wrongpassword")).status_code == 401
            assert_429(await login(evil, password="wrongpassword"))

        assert (await login(client)).status_code == 200

    async def test_successful_logins_do_not_count(self, client):
        await signup(client)
        for _ in range(rate_limit_settings.LOGIN_MAX_FAILURES + 1):
            assert (await login(client)).status_code == 200

    async def test_successful_login_resets_failure_count(self, client):
        await signup(client)
        for _ in range(rate_limit_settings.LOGIN_MAX_FAILURES - 1):
            await login(client, password="wrongpassword")
        assert (await login(client)).status_code == 200

        for _ in range(rate_limit_settings.LOGIN_MAX_FAILURES - 1):
            assert (await login(client, password="wrongpassword")).status_code == 401
        assert (await login(client)).status_code == 200


class TestLimitRunsBeforeRequestHandling:
    async def test_rejected_request_does_not_open_db_session(self, client):
        from db.config import get_session

        for i in range(10):
            await login(client, username=f"nobody{i}")

        opened = 0
        installed = app.dependency_overrides[get_session]

        async def counting_session():
            nonlocal opened
            opened += 1
            async for session in installed():
                yield session

        app.dependency_overrides[get_session] = counting_session
        assert_429(await login(client, username="x"))
        assert opened == 0

    async def test_rejected_request_body_is_not_validated(self, client):
        for _ in range(5):
            await client.post(f"{BASE}/auth/signup", json={})
        # Over the limit, an invalid body gets 429 rather than 422.
        assert_429(await client.post(f"{BASE}/auth/signup", json={}))

    async def test_rejected_form_is_not_parsed(self, client):
        for i in range(10):
            await login(client, username=f"nobody{i}")
        resp = await client.post(f"{BASE}/auth/token", content=b"\xff\xfe not a form")
        assert_429(resp)


def _api_routes(routes):
    """Yield every APIRoute, descending into included routers."""
    from fastapi.routing import APIRoute

    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        # FastAPI keeps included routers as nested route objects.
        nested = getattr(route, "original_router", None) or getattr(route, "routes", None)
        if nested is not None:
            yield from _api_routes(getattr(nested, "routes", nested))


# Every /auth route and the per-IP limit it must enforce. A new /auth route
# fails the first test until it is added here, with or without a limit.
EXPECTED_AUTH_LIMITS = {
    "/auth/login": rate_limit_settings.LOGIN,
    "/auth/token": rate_limit_settings.LOGIN,
    "/auth/signup": rate_limit_settings.SIGNUP,
    "/auth/password-reset/request": rate_limit_settings.PASSWORD_RESET_REQUEST,
    "/auth/password-reset/confirm": rate_limit_settings.PASSWORD_RESET_CONFIRM,
    "/auth/refresh": rate_limit_settings.REFRESH,
}


def test_auth_routes_match_expected_set():
    auth_paths = {r.path for r in _api_routes(app.routes) if r.path.startswith("/auth/")}
    assert auth_paths == set(EXPECTED_AUTH_LIMITS)


@pytest.mark.parametrize("path", sorted(EXPECTED_AUTH_LIMITS))
async def test_auth_route_enforces_its_limit(client, path):
    """
    Sends requests until the limit and expects a 429 after it. This checks
    what the served route does, so a missing route_class or a limit decorator
    placed above @router.post is caught, not just a missing tag.
    """
    (item,) = parse_many(EXPECTED_AUTH_LIMITS[path])
    for _ in range(item.amount):
        resp = await client.post(f"{BASE}{path}", json={})
        assert resp.status_code != 429
    assert_429(await client.post(f"{BASE}{path}", json={}))
