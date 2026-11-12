"""
Per-client-IP rate limiting for the auth endpoints.

A limit is attached to an endpoint with a decorator (``@login_rate_limit``)
and enforced by ``RateLimitedRoute``, the router's route class. The check runs
before FastAPI reads the body or resolves dependencies, so a rejected request
never parses a form or opens a database session.

Limits are kept in process memory, which is correct for the single uvicorn
worker the Dockerfile runs. With several workers or replicas each process
would count on its own; switch the storage to a shared store (Redis) before
scaling out.

The client IP is ``request.client.host``. Behind a reverse proxy, uvicorn
must run with ``--proxy-headers --forwarded-allow-ips=<proxy ip>`` so that
value is the real client and not the proxy; the Dockerfile does this with the
``FORWARDED_ALLOW_IPS`` environment variable. X-Forwarded-For is deliberately
not read here, because any client can set it.
"""

import ipaddress
import math
import os
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, TypeVar

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from limits import RateLimitItem, parse_many
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from pydantic import BaseModel, ConfigDict, field_validator

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
    "LOGIN_MAX_FAILURES_PER_USERNAME": "LOGIN_MAX_FAILURES_PER_USERNAME",
    "LOGIN_MAX_USERNAMES_PER_CLIENT": "LOGIN_MAX_USERNAMES_PER_CLIENT",
    "LOGIN_THROTTLE_MAX_TRACKED": "LOGIN_THROTTLE_MAX_TRACKED",
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
    # Failed logins per (username, client IP).
    LOGIN_MAX_FAILURES: int = 5
    LOGIN_FAILURE_WINDOW_SECONDS: int = 900
    # Failed logins per username across all IPs; past this, only IPs that
    # have failed on the username are blocked.
    LOGIN_MAX_FAILURES_PER_USERNAME: int = 50
    # Distinct usernames a client (IPv4 address or IPv6 /64) may have recent
    # failures on; past this it is refused new usernames.
    LOGIN_MAX_USERNAMES_PER_CLIENT: int = 20
    # Entries per throttle counter. A failed login costs roughly 600 bytes to
    # 1 KB across both counters, so 200,000 is about 120-200 MB when full.
    LOGIN_THROTTLE_MAX_TRACKED: int = 200_000

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

    @field_validator(
        "LOGIN_MAX_FAILURES",
        "LOGIN_FAILURE_WINDOW_SECONDS",
        "LOGIN_MAX_FAILURES_PER_USERNAME",
        "LOGIN_MAX_USERNAMES_PER_CLIENT",
        "LOGIN_THROTTLE_MAX_TRACKED",
    )
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

_ROUTE_LIMIT_ATTR = "__route_rate_limit__"

EndpointT = TypeVar("EndpointT", bound=Callable[..., Any])


_UNKNOWN_CLIENT = "unknown"
# One IPv6 customer usually gets at least a /64, so rotating addresses inside
# it must not give a fresh budget.
_IPV6_CLIENT_PREFIX = 64


def normalize_client_address(host: str | None) -> str:
    """
    Rate-limit key for a connection address.

    IPv4 stays as is, IPv4-mapped IPv6 becomes the IPv4 address, other IPv6
    becomes its /64 network. Missing or unparsable values share one key.
    """
    if not host:
        return _UNKNOWN_CLIENT
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return _UNKNOWN_CLIENT
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        network = ipaddress.IPv6Network(
            (address.packed, _IPV6_CLIENT_PREFIX), strict=False
        )
        return str(network)
    return str(address)


def client_ip(request: Request) -> str:
    """
    Client key for the IP limits and the login throttle.

    Based on the connection address; see the module docstring about proxies.
    """
    host = request.client.host if request.client is not None else None
    return normalize_client_address(host)


class RateLimiter:
    """Fixed-window counters per (scope, client IP), held in memory."""

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self._storage = MemoryStorage()
        self._strategy = FixedWindowRateLimiter(self._storage)

    def limit(self, value: str) -> Callable[[EndpointT], EndpointT]:
        """Limit one endpoint on its own budget."""

        def decorator(endpoint: EndpointT) -> EndpointT:
            scope = f"{endpoint.__module__}.{endpoint.__qualname__}"
            return _attach(endpoint, RouteLimit(self, tuple(parse_many(value)), scope))

        return decorator

    def shared_limit(self, value: str, scope: str) -> Callable[[EndpointT], EndpointT]:
        """Limit several endpoints on one budget, identified by ``scope``."""
        route_limit = RouteLimit(self, tuple(parse_many(value)), scope)

        def decorator(endpoint: EndpointT) -> EndpointT:
            return _attach(endpoint, route_limit)

        return decorator

    def hit(self, items: tuple[RateLimitItem, ...], scope: str, key: str) -> int | None:
        """
        Count one request; return seconds to wait if it is over a limit.

        A rejected request does not use up budget in the other limits. The
        calls do not await, so test-then-hit is atomic on the event loop.
        """
        if not self.enabled:
            return None
        exceeded = [item for item in items if not self._strategy.test(item, scope, key)]
        if exceeded:
            return max(self._seconds_until_reset(item, scope, key) for item in exceeded)
        for item in items:
            self._strategy.hit(item, scope, key)
        return None

    def reset(self) -> None:
        self._storage.reset()

    def _seconds_until_reset(self, item: RateLimitItem, scope: str, key: str) -> int:
        reset_at, _remaining = self._strategy.get_window_stats(item, scope, key)
        return max(1, math.ceil(reset_at - time.time()))


@dataclass(frozen=True)
class RouteLimit:
    limiter: RateLimiter
    items: tuple[RateLimitItem, ...]
    scope: str

    def check(self, request: Request) -> int | None:
        return self.limiter.hit(self.items, self.scope, client_ip(request))


def _attach(endpoint: EndpointT, route_limit: RouteLimit) -> EndpointT:
    if hasattr(endpoint, _ROUTE_LIMIT_ATTR):
        raise RuntimeError(f"{endpoint.__qualname__} already has a rate limit")
    setattr(endpoint, _ROUTE_LIMIT_ATTR, route_limit)
    return endpoint


def route_limit_of(endpoint: Callable[..., Any]) -> RouteLimit | None:
    return getattr(endpoint, _ROUTE_LIMIT_ATTR, None)


class RateLimitedRoute(APIRoute):
    """
    Route class that enforces the endpoint's rate limit first.

    FastAPI reads and parses the body before it resolves dependencies, so a
    dependency would run too late. Wrapping the route handler runs the check
    before any of that work.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        route_limit = route_limit_of(self.endpoint)
        if route_limit is None:
            return handler

        async def rate_limited_handler(request: Request) -> Response:
            retry_after = route_limit.check(request)
            if retry_after is not None:
                return too_many_requests_response(retry_after)
            return await handler(request)

        return rate_limited_handler


limiter = RateLimiter(enabled=rate_limit_settings.ENABLED)

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
    """429 with a generic body and Retry-After; never echoes the limit or key."""
    return JSONResponse(
        status_code=429,
        content={"detail": TOO_MANY_REQUESTS_DETAIL},
        headers={"Retry-After": str(max(1, retry_after_seconds))},
    )
