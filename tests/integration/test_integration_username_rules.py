"""
Username rules end to end: new usernames are strict, existing (legacy)
usernames keep working for login, password reset and profile updates.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from db.models import Climber, PasswordResetToken, UserScope
from security.hashing import hash_password

BASE = "/api/v1"
PASSWORD = "secret123"
EMOJI_NAME = "anna \U0001f338"
LEGACY_NAMES = [EMOJI_NAME, "anna svensson", "anna@example.se"]


async def signup(client: AsyncClient, username: str):
    return await client.post(
        f"{BASE}/auth/signup",
        json={"username": username, "password": PASSWORD, "firstname": "A", "lastname": "B"},
    )


async def login(client: AsyncClient, username: str, password: str = PASSWORD):
    return await client.post(
        f"{BASE}/auth/login", json={"username": username, "password": password}
    )


async def create_legacy_climber(engine, username: str, scope=UserScope.climber) -> int:
    """An account created before the strict rule, stored as it was then."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        climber = Climber(
            username=username,
            password=hash_password(PASSWORD),
            email="legacy@example.se",
            firstname="Legacy",
            lastname="User",
            user_scope=scope,
        )
        session.add(climber)
        await session.commit()
        return climber.id


async def stored_username(engine, climber_id: int) -> str:
    async with async_sessionmaker(engine)() as session:
        return await session.scalar(select(Climber.username).where(Climber.id == climber_id))


async def auth_headers(client, username: str) -> dict:
    resp = await login(client, username)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ---------------------------------------------------------------------------
# Signup: strict rule, normalized storage, look-alike duplicates refused
# ---------------------------------------------------------------------------


class TestSignupUsernameRule:
    async def test_emoji_username_is_rejected(self, client):
        resp = await signup(client, EMOJI_NAME)
        assert resp.status_code == 422
        (error,) = resp.json()["detail"]
        assert error["loc"] == ["body", "username"]
        assert "letters" in error["msg"]

    @pytest.mark.parametrize(
        ("raw", "stored"),
        [
            ("\u00c5sa \u00d6berg", "\u00e5sa \u00f6berg"),
            ("anna@example.se", "anna@example.se"),
            ("anna\u00a0Svensson", "anna svensson"),
            ("climber_2024.x-y", "climber_2024.x-y"),
        ],
    )
    async def test_allowed_username_is_stored_normalized(self, client, raw, stored):
        resp = await signup(client, raw)
        assert resp.status_code == 201, resp.text
        assert resp.json()["climber"]["username"] == stored

    @pytest.mark.parametrize(
        ("first", "look_alike"),
        [
            ("admin", "\uff41dmin"),  # fullwidth a
            ("jos\u00e9", "jose\u0301"),  # precomposed vs decomposed
            ("jose\u0301", "JOS\u00c9"),
        ],
    )
    async def test_look_alike_of_existing_username_is_409(self, client, first, look_alike):
        assert (await signup(client, first)).status_code == 201
        resp = await signup(client, look_alike)
        assert resp.status_code == 409
        assert resp.json() == {"detail": "Username is already taken"}


# ---------------------------------------------------------------------------
# Login and password reset stay lenient for existing accounts
# ---------------------------------------------------------------------------


class TestLegacyUsernamesKeepWorking:
    @pytest.mark.parametrize("username", LEGACY_NAMES)
    async def test_legacy_user_can_log_in(self, client, engine, username):
        await create_legacy_climber(engine, username)
        assert (await login(client, username)).status_code == 200
        assert (await login(client, f"  {username.upper()} ")).status_code == 200

    @pytest.mark.parametrize("username", LEGACY_NAMES)
    async def test_legacy_user_can_use_token_endpoint(self, client, engine, username):
        await create_legacy_climber(engine, username)
        resp = await client.post(
            f"{BASE}/auth/token", data={"username": username, "password": PASSWORD}
        )
        assert resp.status_code == 200

    @pytest.mark.parametrize("username", LEGACY_NAMES)
    async def test_legacy_user_can_request_password_reset(self, client, engine, username):
        climber_id = await create_legacy_climber(engine, username)
        resp = await client.post(
            f"{BASE}/auth/password-reset/request", json={"username": username}
        )
        assert resp.status_code == 200
        async with async_sessionmaker(engine)() as session:
            token = await session.scalar(
                select(PasswordResetToken).where(PasswordResetToken.user_id == climber_id)
            )
        assert token is not None


# ---------------------------------------------------------------------------
# Profile updates: only a real username change is validated
# ---------------------------------------------------------------------------


class TestUsernameUpdates:
    async def test_update_without_username_keeps_legacy_username(self, client, engine):
        climber_id = await create_legacy_climber(engine, EMOJI_NAME)
        headers = await auth_headers(client, EMOJI_NAME)
        resp = await client.patch(
            f"{BASE}/climber/me", json={"firstname": "Anna"}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert await stored_username(engine, climber_id) == EMOJI_NAME

    @pytest.mark.parametrize("resent", [EMOJI_NAME, "  Anna \U0001f338 "])
    async def test_resending_unchanged_legacy_username_is_accepted(
        self, client, engine, resent
    ):
        climber_id = await create_legacy_climber(engine, EMOJI_NAME)
        headers = await auth_headers(client, EMOJI_NAME)
        resp = await client.patch(
            f"{BASE}/climber/me",
            json={"username": resent, "club": "Klubben"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["club"] == "Klubben"
        assert await stored_username(engine, climber_id) == EMOJI_NAME

    async def test_change_to_invalid_username_is_422(self, client, engine):
        climber_id = await create_legacy_climber(engine, EMOJI_NAME)
        headers = await auth_headers(client, EMOJI_NAME)
        resp = await client.patch(
            f"{BASE}/climber/me", json={"username": "anna \U0001f33a"}, headers=headers
        )
        assert resp.status_code == 422
        (error,) = resp.json()["detail"]
        assert error["loc"] == ["body", "username"]
        assert set(error) == {"loc", "msg", "type"}
        assert await stored_username(engine, climber_id) == EMOJI_NAME

    async def test_change_to_valid_username_is_stored_normalized(self, client, engine):
        climber_id = await create_legacy_climber(engine, EMOJI_NAME)
        headers = await auth_headers(client, EMOJI_NAME)
        resp = await client.patch(
            f"{BASE}/climber/me", json={"username": "\uff21nna.S"}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert await stored_username(engine, climber_id) == "anna.s"

    async def test_change_to_look_alike_of_other_user_is_409(self, client, engine):
        await create_legacy_climber(engine, "admin")
        await create_legacy_climber(engine, "other")
        headers = await auth_headers(client, "other")
        resp = await client.patch(
            f"{BASE}/climber/me", json={"username": "\uff41dmin"}, headers=headers
        )
        assert resp.status_code == 409

    async def test_admin_update_follows_the_same_rule(self, client, engine):
        await create_legacy_climber(engine, "boss", scope=UserScope.admin)
        target = await create_legacy_climber(engine, EMOJI_NAME)
        headers = await auth_headers(client, "boss")

        resp = await client.patch(
            f"{BASE}/climber/{target}",
            json={"username": EMOJI_NAME, "firstname": "Anna"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert await stored_username(engine, target) == EMOJI_NAME

        resp = await client.patch(
            f"{BASE}/climber/{target}", json={"username": "x \U0001f338"}, headers=headers
        )
        assert resp.status_code == 422

        resp = await client.patch(
            f"{BASE}/climber/{target}", json={"username": "Anna S"}, headers=headers
        )
        assert resp.status_code == 200
        assert await stored_username(engine, target) == "anna s"



# ---------------------------------------------------------------------------
# A rename stores exactly what signup would store for the same input
# ---------------------------------------------------------------------------

# Lowercasing before NFKC turns these Greek sigmas into a final sigma, while
# signup (NFKC first) gives a normal sigma.
SIGMA_INPUTS = ["\u03f9limber", "\u0391\u03a3\u02b0", "x\u03f9"]


class TestRenameMatchesSignup:
    @pytest.mark.parametrize("typed", SIGMA_INPUTS)
    async def test_signup_stores_the_strict_form(self, client, typed):
        from schema.climber import validate_new_username

        resp = await signup(client, typed)
        assert resp.status_code == 201, resp.text
        assert resp.json()["climber"]["username"] == validate_new_username(typed)

    @pytest.mark.parametrize("typed", SIGMA_INPUTS)
    async def test_rename_stores_the_same_form_and_can_log_in(self, client, engine, typed):
        """Regression: the update schema lowercased first, so rename stored a final sigma."""
        from schema.climber import validate_new_username

        climber_id = await create_legacy_climber(engine, "renamer")
        headers = await auth_headers(client, "renamer")
        resp = await client.patch(
            f"{BASE}/climber/me", json={"username": typed}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert await stored_username(engine, climber_id) == validate_new_username(typed)
        # The user logs in by typing the same input again.
        assert (await login(client, typed)).status_code == 200

    async def test_admin_rename_stores_the_same_form(self, client, engine):
        from schema.climber import validate_new_username

        await create_legacy_climber(engine, "boss", scope=UserScope.admin)
        target = await create_legacy_climber(engine, "target")
        headers = await auth_headers(client, "boss")
        typed = SIGMA_INPUTS[1]
        resp = await client.patch(
            f"{BASE}/climber/{target}", json={"username": typed}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert await stored_username(engine, target) == validate_new_username(typed)
