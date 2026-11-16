"""
Login and password-reset lookups try the lenient form and the strict (NFKC)
form of the typed username in one query.
"""

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from db.models import Climber, PasswordResetToken
from security.hashing import hash_password

BASE = "/api/v1"
FULLWIDTH_ADMIN = "\uff41dmin"
JOSE_DECOMPOSED = "jose\u0301"
JOSE_PRECOMPOSED = "jos\u00e9"


async def signup(client, username, password="secret123", email=None):
    body = {"username": username, "password": password, "firstname": "A", "lastname": "B"}
    if email:
        body["email"] = email
    resp = await client.post(f"{BASE}/auth/signup", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["climber"]["id"]


async def login(client, username, password="secret123"):
    return await client.post(
        f"{BASE}/auth/login", json={"username": username, "password": password}
    )


async def token_login(client, username, password="secret123"):
    return await client.post(
        f"{BASE}/auth/token", data={"username": username, "password": password}
    )


async def create_legacy_climber(engine, username, password="secret123", email=None):
    """A row stored as-is, from before the strict rule."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        climber = Climber(
            username=username,
            password=hash_password(password),
            email=email,
            firstname="Legacy",
            lastname="User",
        )
        session.add(climber)
        await session.commit()
        return climber.id


async def reset_token_owner(engine):
    async with async_sessionmaker(engine)() as session:
        return await session.scalar(select(PasswordResetToken.user_id))


class TestLoginFallback:
    @pytest.mark.parametrize("typed", [FULLWIDTH_ADMIN, "admin", "  \uff21DMIN "])
    async def test_fullwidth_signup_logs_in_with_typed_or_folded_form(self, client, typed):
        await signup(client, FULLWIDTH_ADMIN)  # stored as "admin"
        assert (await login(client, typed)).status_code == 200
        assert (await token_login(client, typed)).status_code == 200

    @pytest.mark.parametrize("typed", [JOSE_DECOMPOSED, JOSE_PRECOMPOSED])
    async def test_decomposed_signup_logs_in_with_either_form(self, client, typed):
        await signup(client, JOSE_DECOMPOSED)  # stored precomposed
        assert (await login(client, typed)).status_code == 200

    async def test_legacy_and_new_look_alikes_each_reach_their_own_account(
        self, client, engine
    ):
        legacy_id = await create_legacy_climber(engine, JOSE_DECOMPOSED, password="legacy-pw")
        new_id = await signup(client, JOSE_PRECOMPOSED, password="new-password")
        assert legacy_id != new_id

        # Typed exactly, each name reaches its own account.
        assert (await login(client, JOSE_DECOMPOSED, "legacy-pw")).status_code == 200
        assert (await login(client, JOSE_DECOMPOSED, "new-password")).status_code == 401
        assert (await login(client, JOSE_PRECOMPOSED, "new-password")).status_code == 200
        assert (await login(client, JOSE_PRECOMPOSED, "legacy-pw")).status_code == 401

    async def test_unknown_username_still_fails(self, client):
        assert (await login(client, FULLWIDTH_ADMIN)).status_code == 401


class TestPasswordResetLookupFallback:
    async def test_reset_finds_account_by_typed_fullwidth_name(self, client, engine):
        climber_id = await signup(client, FULLWIDTH_ADMIN, email="admin@example.se")
        resp = await client.post(
            f"{BASE}/auth/password-reset/request", json={"username": FULLWIDTH_ADMIN}
        )
        assert resp.status_code == 200
        assert await reset_token_owner(engine) == climber_id

    async def test_reset_prefers_exact_legacy_match(self, client, engine):
        legacy_id = await create_legacy_climber(
            engine, JOSE_DECOMPOSED, email="legacy@example.se"
        )
        await signup(client, JOSE_PRECOMPOSED, email="new@example.se")
        resp = await client.post(
            f"{BASE}/auth/password-reset/request", json={"username": JOSE_DECOMPOSED}
        )
        assert resp.status_code == 200
        assert await reset_token_owner(engine) == legacy_id


class TestLookupQueryCount:
    """The same number of statements for existing and unknown usernames."""

    @pytest.fixture()
    def statements(self, engine):
        seen: list[str] = []

        def record(_conn, _cursor, statement, _params, _context, _executemany):
            seen.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        yield seen
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    async def _count(self, statements, request):
        statements.clear()
        await request
        return len(statements)

    async def test_failed_login_query_count_does_not_reveal_existence(
        self, client, engine, statements
    ):
        await signup(client, FULLWIDTH_ADMIN)
        existing = await self._count(statements, login(client, FULLWIDTH_ADMIN, "wrong-pw"))
        unknown = await self._count(statements, login(client, "nobody-here", "wrong-pw"))
        assert existing == unknown == 1

    async def test_reset_lookup_query_count_does_not_reveal_existence(
        self, client, engine, statements
    ):
        await signup(client, FULLWIDTH_ADMIN)  # no email: nothing to send
        existing = await self._count(
            statements,
            client.post(f"{BASE}/auth/password-reset/request", json={"username": "admin"}),
        )
        unknown = await self._count(
            statements,
            client.post(f"{BASE}/auth/password-reset/request", json={"username": "nobody"}),
        )
        assert existing == unknown == 1


class TestThrottleSharesKeyAcrossVariants:
    """Every form that reaches an account counts against one throttle key."""

    @pytest.fixture(autouse=True)
    def throttle_on(self, monkeypatch):
        from security.login_throttle import login_throttle
        from security.rate_limit import limiter

        monkeypatch.setattr(limiter, "enabled", True)
        monkeypatch.setattr(login_throttle, "enabled", True)

    async def test_failures_spread_over_fullwidth_variants_share_the_limit(self, client):
        """
        Regression: keyed on the lenient form, "admin", fullwidth "admin" and
        each mixed variant had their own counters, multiplying the limits.
        """
        from security.rate_limit import rate_limit_settings

        assert rate_limit_settings.LOGIN_MAX_FAILURES == 5
        await signup(client, "admin")
        variants = ["admin", "\uff41dmin", "a\uff44min", "ad\uff4din", "adm\uff49n"]
        for variant in variants:
            assert (await login(client, variant, "wrong-pw")).status_code == 401
        # Any variant, even with the correct password, is now refused.
        for typed in ("admin", "admi\uff4e"):
            resp = await login(client, typed)
            assert resp.status_code == 429
            assert int(resp.headers["Retry-After"]) >= 1

    async def test_token_endpoint_shares_the_same_key(self, client):
        await signup(client, "admin")
        for _ in range(5):
            assert (await login(client, "\uff41dmin", "wrong-pw")).status_code == 401
        resp = await token_login(client, "admin")
        assert resp.status_code == 429
