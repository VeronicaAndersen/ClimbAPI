import os
from datetime import timedelta

from pydantic import BaseModel, field_validator

# Pinned so the signing algorithm cannot be switched through the environment.
JWT_ALG = "HS256"
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


class Settings(BaseModel):
    JWT_SECRET: str
    JWT_ALG: str = JWT_ALG
    ACCESS_TTL: int = int(os.getenv("ACCESS_TTL_MIN", "15"))
    REFRESH_TTL: int = int(os.getenv("REFRESH_TTL_DAYS", "7"))
    ISSUER: str = os.getenv("JWT_ISSUER", "climb-api")

    @field_validator("JWT_SECRET")
    @classmethod
    def jwt_secret_must_be_strong(cls, value: str) -> str:
        return validate_jwt_secret(value)

    @field_validator("JWT_ALG")
    @classmethod
    def jwt_alg_is_pinned(cls, value: str) -> str:
        if value != JWT_ALG:
            raise RuntimeError(f"JWT algorithm must be {JWT_ALG}.")
        return value

    @property
    def access_delta(self) -> timedelta:
        return timedelta(minutes=self.ACCESS_TTL)

    @property
    def refresh_delta(self) -> timedelta:
        return timedelta(days=self.REFRESH_TTL)


def load_settings() -> Settings:
    return Settings(JWT_SECRET=validate_jwt_secret(os.getenv("JWT_SECRET")))


settings = load_settings()
