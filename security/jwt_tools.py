from datetime import datetime, timezone
from typing import Any, Dict, Optional

import jwt

from schema.setting import settings


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def create_access_token(sub: str | int, extra: Optional[Dict[str, Any]] = None) -> str:
    now = _now()
    payload = {
        "iss": settings.ISSUER,
        "sub": str(sub),
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + settings.access_delta).timestamp()),
        "type": "access",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def create_refresh_token(sub: str | int, jti: Optional[str] = None) -> str:
    # jti optional if we later want rotation/blacklisting
    now = _now()
    payload = {
        "iss": settings.ISSUER,
        "sub": str(sub),
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + settings.refresh_delta).timestamp()),
        "type": "refresh",
    }
    if jti:
        payload["jti"] = jti
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def decode_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.JWT_SECRET,
        algorithms=[settings.JWT_ALG],
        options={"require": ["exp", "iat", "nbf", "sub"]},
        leeway=10,  # leeway handles small clock skews
        issuer=settings.ISSUER,
    )
