"""
Per-client-IP rate limiting for the auth endpoints.

A limit is attached to an endpoint with a decorator (``@login_rate_limit``)
and enforced by ``RateLimitedRoute``, the router's route class. The check runs
before FastAPI reads the body or resolves dependencies, so a rejected request
never parses a form or opens a database session.

Limits are kept in process memory, which is correct for the single uvicorn
worker the Dockerfile runs. With several workers or replicas each process
counts on its own. LOGIN_THROTTLE_BACKEND=postgres shares the login throttle
and LOGIN_IP_LIMIT_BACKEND=postgres adds a shared per-IP limit for the login
routes; the other per-IP route limits stay per process.

The client key comes from ``security.client_address.client_ip``, based on
``request.client.host``. Behind a reverse proxy, uvicorn
must run with ``--proxy-headers --forwarded-allow-ips=<proxy ip>`` so that
value is the real client and not the proxy; the Dockerfile does this with the
``FORWARDED_ALLOW_IPS`` environment variable. X-Forwarded-For is deliberately
not read here, because any client can set it.

On Vercel there is no uvicorn in front to configure, and the connection
address is the platform's. With TRUST_VERCEL_HEADERS the client key comes
from the ``x-real-ip`` header that Vercel's edge sets (overwriting any value
the client sent). The setting is only accepted on a Vercel deployment
(VERCEL=1 and VERCEL_ENV production or preview), so it cannot be switched on
by mistake where clients can set that header themselves, including a local
run with variables from `vercel env pull`.
"""

import math
import os
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from limits import RateLimitItem, parse_many
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from pydantic import BaseModel, ConfigDict, field_validator

from security.client_address import client_ip
from security.client_address import configure as configure_client_address

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
    "TRUST_VERCEL_HEADERS": "TRUST_VERCEL_HEADERS",
    "LOGIN_THROTTLE_BACKEND": "LOGIN_THROTTLE_BACKEND",
    "LOGIN_THROTTLE_DB_TIMEOUT_SECONDS": "LOGIN_THROTTLE_DB_TIMEOUT_SECONDS",
    "LOGIN_THROTTLE_LOAD_CACHE_SECONDS": "LOGIN_THROTTLE_LOAD_CACHE_SECONDS",
    "LOGIN_IP_LIMIT_BACKEND": "LOGIN_IP_LIMIT_BACKEND",
    "LOGIN_THROTTLE_SWEEP_BATCH": "LOGIN_THROTTLE_SWEEP_BATCH",
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
    "LOGIN_THROTTLE_PRESSURE_RATIO": "LOGIN_THROTTLE_PRESSURE_RATIO",
    "LOGIN_MAX_FAILURES_PER_USERNAME_PREFIX": "LOGIN_MAX_FAILURES_PER_USERNAME_PREFIX",
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
    # Failed logins per (username, IPv6 /48); past this every /64 in the /48
    # is blocked for the username. IPv4 clients are not affected.
    LOGIN_MAX_FAILURES_PER_USERNAME_PREFIX: int = 15
    # Distinct usernames a client (IPv4 address or IPv6 /64) may have recent
    # failures on; past this it is refused new usernames. Only enforced under
    # pressure, see LOGIN_THROTTLE_PRESSURE_RATIO.
    LOGIN_MAX_USERNAMES_PER_CLIENT: int = 20
    # Entries per throttle counter. A failed login costs about 550 bytes to
    # 1 KB across the counters (IPv6 at the high end), so at most ~210 MB.
    LOGIN_THROTTLE_MAX_TRACKED: int = 200_000
    # Share of LOGIN_THROTTLE_MAX_TRACKED from which the per-client username
    # cap applies. 0 enforces it always, 1 only when a counter is full.
    LOGIN_THROTTLE_PRESSURE_RATIO: float = 0.1
    # Take the client IP from Vercel's x-real-ip header instead of the
    # connection address. Only allowed when running on Vercel (VERCEL=1).
    TRUST_VERCEL_HEADERS: bool = False
    # Where the login throttle keeps its counters: "memory" (this process),
    # "postgres" (shared by all instances, security/login_throttle_postgres.py)
    # or "shadow" (memory decides, Postgres runs alongside and the two are
    # compared, security/login_throttle_shadow.py).
    LOGIN_THROTTLE_BACKEND: Literal["memory", "postgres", "shadow"] = "memory"
    # For the Postgres backend and the shared login IP limit: upper bound for
    # each query (the optional load reading gets a third of it).
    LOGIN_THROTTLE_DB_TIMEOUT_SECONDS: float = 3.0
    # For the Postgres backend: how long a load reading may be reused.
    LOGIN_THROTTLE_LOAD_CACHE_SECONDS: float = 5.0
    # "postgres" adds a per-IP LOGIN limit shared by all instances (after the
    # in-memory one), for hosting without a shared rate-limit layer such as
    # WAF rules. Uses LOGIN_THROTTLE_DB_TIMEOUT_SECONDS.
    LOGIN_IP_LIMIT_BACKEND: Literal["memory", "postgres"] = "memory"
    # Postgres backends: expired rows deleted per call (every login acquire
    # and finish, every shared IP-limit check). Bounded so one sweep stays a
    # few milliseconds on the index; larger than the attack-free login rate.
    LOGIN_THROTTLE_SWEEP_BATCH: int = 200

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

    @field_validator("LOGIN_THROTTLE_DB_TIMEOUT_SECONDS", "LOGIN_THROTTLE_LOAD_CACHE_SECONDS")
    @classmethod
    def must_be_positive_seconds(cls, value: float) -> float:
        if not value > 0:
            raise ValueError("must be a positive number of seconds")
        return value

    @field_validator("LOGIN_THROTTLE_PRESSURE_RATIO")
    @classmethod
    def must_be_a_ratio(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("must be between 0 and 1")
        return value

    @field_validator(
        "LOGIN_MAX_FAILURES",
        "LOGIN_FAILURE_WINDOW_SECONDS",
        "LOGIN_MAX_FAILURES_PER_USERNAME",
        "LOGIN_MAX_USERNAMES_PER_CLIENT",
        "LOGIN_THROTTLE_MAX_TRACKED",
        "LOGIN_MAX_FAILURES_PER_USERNAME_PREFIX",
        "LOGIN_THROTTLE_SWEEP_BATCH",
    )
    @classmethod
    def must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be at least 1")
        return value


_VERCEL_DEPLOYED_ENVS = frozenset({"production", "preview"})


def load_rate_limit_settings() -> RateLimitSettings:
    """Read overrides from the environment; invalid values fail at startup."""
    overrides = {
        field: value
        for field, env_var in _ENV_VARS.items()
        if (value := os.getenv(env_var)) is not None
    }
    settings = RateLimitSettings(**overrides)
    # Anywhere but a Vercel deployment a client could send its own x-real-ip
    # and pick its rate-limit and throttle key freely. VERCEL=1 alone is not
    # proof: `vercel env pull` writes it into local env files, with
    # VERCEL_ENV=development. Deployed functions get production or preview.
    on_vercel_deployment = (
        os.getenv("VERCEL") == "1" and os.getenv("VERCEL_ENV") in _VERCEL_DEPLOYED_ENVS
    )
    if settings.TRUST_VERCEL_HEADERS and not on_vercel_deployment:
        raise RuntimeError(
            "TRUST_VERCEL_HEADERS is only allowed on a Vercel deployment "
            "(VERCEL=1 and VERCEL_ENV=production or preview). Elsewhere, leave it "
            "unset and use uvicorn's FORWARDED_ALLOW_IPS."
        )
    return settings


rate_limit_settings = load_rate_limit_settings()
configure_client_address(trust_vercel_headers=rate_limit_settings.TRUST_VERCEL_HEADERS)

_ROUTE_LIMIT_ATTR = "__route_rate_limit__"

EndpointT = TypeVar("EndpointT", bound=Callable[..., Any])


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

    def shared_limit(
        self,
        value: str,
        scope: str,
        shared: "Callable[[str], Awaitable[int | None]] | None" = None,
    ) -> Callable[[EndpointT], EndpointT]:
        """
        Limit several endpoints on one budget, identified by ``scope``.

        ``shared`` is an optional second check, counted across instances
        (e.g. in Postgres), run only when the in-memory check passes.
        """
        route_limit = RouteLimit(self, tuple(parse_many(value)), scope, shared)

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
    shared: "Callable[[str], Awaitable[int | None]] | None" = None

    async def check(self, request: Request) -> int | None:
        key = client_ip(request)
        retry_after = self.limiter.hit(self.items, self.scope, key)
        if retry_after is not None or self.shared is None or not self.limiter.enabled:
            return retry_after
        return await self.shared(key)


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
            retry_after = await route_limit.check(request)
            if retry_after is not None:
                return too_many_requests_response(retry_after)
            return await handler(request)

        return rate_limited_handler


limiter = RateLimiter(enabled=rate_limit_settings.ENABLED)

def _shared_login_ip_limit(
    settings: RateLimitSettings,
) -> "Callable[[str], Awaitable[int | None]] | None":
    """The shared per-IP login limit when LOGIN_IP_LIMIT_BACKEND=postgres."""
    if settings.LOGIN_IP_LIMIT_BACKEND != "postgres":
        return None
    # Imported here: the default setup must not need a database.
    from db.config import async_engine
    from security.login_ip_limit_postgres import PostgresIpLimit, require_asyncpg_engine

    require_asyncpg_engine(async_engine, "LOGIN_IP_LIMIT_BACKEND=postgres")
    return PostgresIpLimit(
        async_engine,
        tuple(parse_many(settings.LOGIN)),
        db_timeout_seconds=settings.LOGIN_THROTTLE_DB_TIMEOUT_SECONDS,
        sweep_batch=settings.LOGIN_THROTTLE_SWEEP_BATCH,
    ).hit


# /login and /token accept the same credentials, so they share one budget.
login_rate_limit = limiter.shared_limit(
    rate_limit_settings.LOGIN,
    scope="auth-login",
    shared=_shared_login_ip_limit(rate_limit_settings),
)
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
