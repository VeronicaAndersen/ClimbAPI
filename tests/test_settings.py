"""Unit tests for schema/setting.py"""

import pytest

from schema.setting import JWT_ALG, Settings, load_settings


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
