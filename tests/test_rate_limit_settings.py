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
