"""
Per-client-IP rate limiting for the auth endpoints.

Limits are kept in process memory, which is correct for the single uvicorn
worker the Dockerfile runs. With several workers or replicas each process
would count on its own; switch ``storage_uri`` to a shared store (Redis)
before scaling out.

The client IP is ``request.client.host``. Behind a reverse proxy, run uvicorn
with ``--proxy-headers --forwarded-allow-ips=<proxy ip>`` so that value is the
real client and not the proxy. X-Forwarded-For is deliberately not read here,
because any client can set it.
"""

import math
import os
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from limits import parse_many
from pydantic import BaseModel, ConfigDict, field_validator
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

TOO_MANY_REQUESTS_DETAIL = "Too many requests. Please try again later."

# OpenAPI entry for rate-limited routes: `@router.post(..., responses=RATE_LIMITED_RESPONSE)`.
RATE_LIMITED_RESPONSE: dict[int | str, dict[str, object]] = {
    429: {
        "description": "Too many requests",
        "headers": {
            "Retry-After": {
                "description": "Seconds to wait before retrying",
                "schema": {"type": "integer"},
            }
        },
    }
}


# Setting name -> environment variable.
_ENV_VARS = {
    "ENABLED": "RATE_LIMIT_ENABLED",
    "LOGIN": "RATE_LIMIT_LOGIN",
    "SIGNUP": "RATE_LIMIT_SIGNUP",
    "PASSWORD_RESET_REQUEST": "RATE_LIMIT_PASSWORD_RESET_REQUEST",
    "PASSWORD_RESET_CONFIRM": "RATE_LIMIT_PASSWORD_RESET_CONFIRM",
    "REFRESH": "RATE_LIMIT_REFRESH",
    "LOGIN_MAX_FAILURES": "LOGIN_MAX_FAILURES",
    "LOGIN_FAILURE_WINDOW_SECONDS": "LOGIN_FAILURE_WINDOW_SECONDS",
}


class RateLimitSettings(BaseModel):
    model_config = ConfigDict(frozen=True, validate_default=True)

    ENABLED: bool = True
    # Per client IP. /login and /token share the LOGIN budget.
    LOGIN: str = "10/minute"
    SIGNUP: str = "5/minute"
    PASSWORD_RESET_REQUEST: str = "3/minute"
    PASSWORD_RESET_CONFIRM: str = "10/minute"
    REFRESH: str = "30/minute"
    # Per username, across all IPs.
    LOGIN_MAX_FAILURES: int = 5
    LOGIN_FAILURE_WINDOW_SECONDS: int = 900

    @field_validator(
        "LOGIN", "SIGNUP", "PASSWORD_RESET_REQUEST", "PASSWORD_RESET_CONFIRM", "REFRESH"
    )
    @classmethod
    def must_be_valid_limit(cls, value: str) -> str:
        try:
            parse_many(value)
        except ValueError as e:
            raise ValueError(f"Invalid rate limit string: {value!r}") from e
        return value

    @field_validator("LOGIN_MAX_FAILURES", "LOGIN_FAILURE_WINDOW_SECONDS")
    @classmethod
    def must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be at least 1")
        return value


def load_rate_limit_settings() -> RateLimitSettings:
    """Read overrides from the environment; invalid values fail at startup."""
    overrides = {
        field: value
        for field, env_var in _ENV_VARS.items()
        if (value := os.getenv(env_var)) is not None
    }
    return RateLimitSettings(**overrides)


rate_limit_settings = load_rate_limit_settings()

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="memory://",
    enabled=rate_limit_settings.ENABLED,
)

# /login and /token accept the same credentials, so they share one budget.
login_rate_limit = limiter.shared_limit(rate_limit_settings.LOGIN, scope="auth-login")
signup_rate_limit = limiter.limit(rate_limit_settings.SIGNUP)
password_reset_request_rate_limit = limiter.limit(
    rate_limit_settings.PASSWORD_RESET_REQUEST
)
password_reset_confirm_rate_limit = limiter.limit(
    rate_limit_settings.PASSWORD_RESET_CONFIRM
)
refresh_rate_limit = limiter.limit(rate_limit_settings.REFRESH)


def too_many_requests_response(retry_after_seconds: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": TOO_MANY_REQUESTS_DETAIL},
        headers={"Retry-After": str(max(1, retry_after_seconds))},
    )


def _retry_after_seconds(request: Request, exc: RateLimitExceeded) -> int:
    view_limit = getattr(request.state, "view_rate_limit", None)
    if view_limit is not None:
        item, keys = view_limit
        reset_at, _remaining = limiter.limiter.get_window_stats(item, *keys)
        return math.ceil(reset_at - time.time())
    return int(exc.limit.limit.get_expiry())


async def rate_limit_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    """429 with a generic body and Retry-After; never echoes the limit or key."""
    if not isinstance(exc, RateLimitExceeded):  # pragma: no cover - registration guard
        raise exc
    return too_many_requests_response(_retry_after_seconds(request, exc))
