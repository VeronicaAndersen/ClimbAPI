"""Unit tests for security/hashing.py"""
import pytest
from security.hashing import hash_password, verify_password, needs_rehash


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
