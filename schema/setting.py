import os
from datetime import timedelta

from pydantic import BaseModel, field_validator

# Pinned so the signing algorithm cannot be switched through the environment.
JWT_ALG = "HS256"
_JWT_ALG_ENV = "JWT_ALG"
MIN_JWT_SECRET_BYTES = 32
_REMOVED_DEV_SECRET = "CHANGE_ME_DEV_ONLY"


def validate_jwt_secret(secret: str | None) -> str:
    """Reject a missing, public, or short HMAC secret before any token is signed."""
    if secret is None or secret == "" or secret == _REMOVED_DEV_SECRET:
        raise RuntimeError(
            "JWT_SECRET is required. Set it to a random value of at least "
            f"{MIN_JWT_SECRET_BYTES} bytes. The previous public default is not accepted."
        )
    if len(secret.encode("utf-8")) < MIN_JWT_SECRET_BYTES:
        raise RuntimeError(
            f"JWT_SECRET must be at least {MIN_JWT_SECRET_BYTES} bytes."
        )
    return secret


def check_jwt_alg_env(value: str | None) -> None:
    """
    Refuse a JWT_ALG environment variable that asks for another algorithm.

    The algorithm is pinned in code. Ignoring a different value silently
    would let an operator believe tokens use an algorithm they do not.
    """
    if value is not None and value != JWT_ALG:
        raise RuntimeError(
            f"{_JWT_ALG_ENV}={value!r} is not supported. Tokens are always signed "
            f"with {JWT_ALG}; unset {_JWT_ALG_ENV} or set it to {JWT_ALG}."
        )


class Settings(BaseModel):
    JWT_SECRET: str
    ACCESS_TTL: int = int(os.getenv("ACCESS_TTL_MIN", "15"))
    REFRESH_TTL: int = int(os.getenv("REFRESH_TTL_DAYS", "7"))
    ISSUER: str = os.getenv("JWT_ISSUER", "climb-api")

    @field_validator("JWT_SECRET")
    @classmethod
    def jwt_secret_must_be_strong(cls, value: str) -> str:
        return validate_jwt_secret(value)

    @property
    def access_delta(self) -> timedelta:
        return timedelta(minutes=self.ACCESS_TTL)

    @property
    def refresh_delta(self) -> timedelta:
        return timedelta(days=self.REFRESH_TTL)


def load_settings() -> Settings:
    check_jwt_alg_env(os.getenv(_JWT_ALG_ENV))
    return Settings(JWT_SECRET=validate_jwt_secret(os.getenv("JWT_SECRET")))


settings = load_settings()
