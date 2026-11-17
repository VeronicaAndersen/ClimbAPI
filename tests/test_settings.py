"""Unit tests for schema/setting.py"""

from unittest.mock import patch

import pytest

from schema import setting
from schema.setting import JWT_ALG, MIN_JWT_SECRET_BYTES, Settings, load_settings


def test_settings_has_no_algorithm_field():
    """The algorithm is the JWT_ALG constant, not a configurable setting."""
    assert "JWT_ALG" not in Settings.model_fields


def test_load_settings_accepts_unset_jwt_alg(monkeypatch):
    monkeypatch.delenv("JWT_ALG", raising=False)
    assert load_settings().JWT_SECRET


def test_load_settings_accepts_pinned_jwt_alg(monkeypatch):
    monkeypatch.setenv("JWT_ALG", JWT_ALG)
    assert load_settings().JWT_SECRET


@pytest.mark.parametrize("alg", ["RS256", "none", "HS512", "hs256", ""])
def test_load_settings_rejects_other_jwt_alg(monkeypatch, alg):
    """Regression: a different JWT_ALG used to be silently ignored."""
    monkeypatch.setenv("JWT_ALG", alg)
    with pytest.raises(RuntimeError, match="JWT_ALG"):
        load_settings()


@pytest.mark.parametrize("secret", [None, "", "CHANGE_ME_DEV_ONLY", "x" * (MIN_JWT_SECRET_BYTES - 1)])
def test_load_settings_rejects_missing_or_weak_secret(monkeypatch, secret):
    if secret is None:
        monkeypatch.delenv("JWT_SECRET", raising=False)
    else:
        monkeypatch.setenv("JWT_SECRET", secret)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        load_settings()


@pytest.mark.parametrize("secret", [None, "short"])
def test_settings_model_rejects_missing_or_weak_secret(secret):
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        Settings(JWT_SECRET=secret)


def test_settings_model_rejects_non_string_secret():
    with pytest.raises(TypeError):
        Settings(JWT_SECRET=b"x" * MIN_JWT_SECRET_BYTES)


def test_secret_is_validated_once(monkeypatch):
    """Regression: load_settings and the field validator both validated it."""
    monkeypatch.setenv("JWT_SECRET", "y" * MIN_JWT_SECRET_BYTES)
    with patch.object(
        setting, "validate_jwt_secret", wraps=setting.validate_jwt_secret
    ) as validate:
        load_settings()
    assert validate.call_count == 1
