"""Unit tests for rate limit settings in security/rate_limit.py"""

import pytest
from pydantic import ValidationError

from security.rate_limit import RateLimitSettings, load_rate_limit_settings


def test_defaults():
    settings = RateLimitSettings()
    assert settings.ENABLED is True
    assert settings.LOGIN == "10/minute"
    assert settings.SIGNUP == "5/minute"
    assert settings.PASSWORD_RESET_REQUEST == "3/minute"
    assert settings.PASSWORD_RESET_CONFIRM == "10/minute"
    assert settings.REFRESH == "30/minute"
    assert settings.LOGIN_MAX_FAILURES == 5
    assert settings.LOGIN_FAILURE_WINDOW_SECONDS == 900
    assert settings.LOGIN_MAX_FAILURES_PER_USERNAME == 50
    assert settings.LOGIN_MAX_USERNAMES_PER_CLIENT == 20
    assert settings.LOGIN_THROTTLE_MAX_TRACKED == 200_000
    assert settings.LOGIN_THROTTLE_PRESSURE_RATIO == 0.1


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_LOGIN", "20/minute;100/hour")
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "8")
    settings = load_rate_limit_settings()
    assert settings.ENABLED is False
    assert settings.LOGIN == "20/minute;100/hour"
    assert settings.LOGIN_MAX_FAILURES == 8


def test_invalid_limit_string_rejected(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_SIGNUP", "lots")
    with pytest.raises(ValidationError):
        load_rate_limit_settings()


@pytest.mark.parametrize(
    "name",
    [
        "LOGIN_MAX_FAILURES",
        "LOGIN_FAILURE_WINDOW_SECONDS",
        "LOGIN_MAX_FAILURES_PER_USERNAME",
        "LOGIN_MAX_USERNAMES_PER_CLIENT",
        "LOGIN_THROTTLE_MAX_TRACKED",
    ],
)
def test_non_positive_throttle_values_rejected(monkeypatch, name):
    monkeypatch.setenv(name, "0")
    with pytest.raises(ValidationError):
        load_rate_limit_settings()


@pytest.mark.parametrize("value", ["-0.1", "1.5"])
def test_pressure_ratio_outside_zero_to_one_rejected(monkeypatch, value):
    monkeypatch.setenv("LOGIN_THROTTLE_PRESSURE_RATIO", value)
    with pytest.raises(ValidationError):
        load_rate_limit_settings()


@pytest.mark.parametrize("value", ["0", "1", "0.25"])
def test_pressure_ratio_bounds_accepted(monkeypatch, value):
    monkeypatch.setenv("LOGIN_THROTTLE_PRESSURE_RATIO", value)
    assert load_rate_limit_settings().LOGIN_THROTTLE_PRESSURE_RATIO == float(value)



def test_login_throttle_backend_defaults(monkeypatch):
    for name in (
        "LOGIN_THROTTLE_BACKEND",
        "LOGIN_THROTTLE_DB_TIMEOUT_SECONDS",
        "LOGIN_THROTTLE_LOAD_CACHE_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = load_rate_limit_settings()
    assert settings.LOGIN_THROTTLE_BACKEND == "memory"
    assert settings.LOGIN_THROTTLE_DB_TIMEOUT_SECONDS == 3.0
    assert settings.LOGIN_THROTTLE_LOAD_CACHE_SECONDS == 5.0


@pytest.mark.parametrize("backend", ["memory", "postgres", "shadow"])
def test_login_throttle_backend_known_values(monkeypatch, backend):
    monkeypatch.setenv("LOGIN_THROTTLE_BACKEND", backend)
    assert load_rate_limit_settings().LOGIN_THROTTLE_BACKEND == backend


@pytest.mark.parametrize("backend", ["redis", "Memory", ""])
def test_login_throttle_backend_unknown_value_rejected(monkeypatch, backend):
    monkeypatch.setenv("LOGIN_THROTTLE_BACKEND", backend)
    with pytest.raises(ValidationError):
        load_rate_limit_settings()


@pytest.mark.parametrize(
    "name", ["LOGIN_THROTTLE_DB_TIMEOUT_SECONDS", "LOGIN_THROTTLE_LOAD_CACHE_SECONDS"]
)
@pytest.mark.parametrize("value", ["0", "-1", "soon", "nan"])
def test_login_throttle_seconds_must_be_positive(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        load_rate_limit_settings()


@pytest.mark.parametrize(
    "name", ["LOGIN_THROTTLE_DB_TIMEOUT_SECONDS", "LOGIN_THROTTLE_LOAD_CACHE_SECONDS"]
)
def test_login_throttle_seconds_accept_fractions(monkeypatch, name):
    monkeypatch.setenv(name, "0.5")
    assert getattr(load_rate_limit_settings(), name) == 0.5
