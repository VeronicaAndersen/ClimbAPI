"""Unit tests for security/hashing.py"""
import asyncio
import threading
import time
from unittest.mock import patch

from security.hashing import (
    hash_password,
    hash_password_async,
    needs_rehash,
    verify_dummy_password_async,
    verify_password,
    verify_password_async,
)


def test_hash_password_returns_string():
    result = hash_password("mysecretpassword")
    assert isinstance(result, str)
    assert len(result) > 0


def test_hash_password_is_salted():
    """Same password produces different hashes due to random salt."""
    hash1 = hash_password("mysecretpassword")
    hash2 = hash_password("mysecretpassword")
    assert hash1 != hash2


def test_verify_password_correct():
    password = "correctpassword"
    hashed = hash_password(password)
    assert verify_password(password, hashed) is True


def test_verify_password_wrong_password():
    hashed = hash_password("correctpassword")
    assert verify_password("wrongpassword", hashed) is False


def test_verify_password_empty_string():
    hashed = hash_password("somepassword")
    assert verify_password("", hashed) is False


def test_verify_password_similar_but_wrong():
    hashed = hash_password("password1")
    assert verify_password("password2", hashed) is False


def test_needs_rehash_fresh_hash():
    """A freshly created hash using current params should not need rehashing."""
    hashed = hash_password("testpassword")
    assert needs_rehash(hashed) is False


# ---------------------------------------------------------------------------
# Async wrappers
# ---------------------------------------------------------------------------

async def test_hash_password_async_produces_verifiable_hash():
    hashed = await hash_password_async("asyncsecret")
    assert verify_password("asyncsecret", hashed) is True


async def test_verify_password_async_correct():
    hashed = hash_password("correctpassword")
    assert await verify_password_async("correctpassword", hashed) is True


async def test_verify_password_async_wrong_password():
    hashed = hash_password("correctpassword")
    assert await verify_password_async("wrongpassword", hashed) is False


async def test_verify_dummy_password_async_always_false():
    assert await verify_dummy_password_async("anything") is False
    assert await verify_dummy_password_async("") is False


async def test_hash_password_async_runs_off_event_loop_thread():
    loop_thread = threading.get_ident()
    seen: list[int] = []

    def fake_hash(plain_password: str) -> str:
        seen.append(threading.get_ident())
        return "hashed"

    with patch("security.hashing.hash_password", side_effect=fake_hash):
        assert await hash_password_async("pw") == "hashed"

    assert seen and seen[0] != loop_thread


async def test_verify_password_async_does_not_block_event_loop():
    """While a slow verification runs, other coroutines keep making progress."""
    def slow_verify(plain_password: str, password_hash: str) -> bool:
        time.sleep(0.2)
        return True

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        with patch("security.hashing.verify_password", side_effect=slow_verify):
            assert await verify_password_async("pw", "hash") is True
    finally:
        task.cancel()

    assert ticks >= 5
