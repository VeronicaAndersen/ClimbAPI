import os
from datetime import timedelta

from pydantic import BaseModel


class Settings(BaseModel):
    JWT_SECRET: str = os.getenv("JWT_SECRET", "CHANGE_ME_DEV_ONLY") #TODO: Put a strong random JWT_SECRET in your env in prod.
    JWT_ALG: str = os.getenv("JWT_ALG", "HS256")
    ACCESS_TTL: int = int(os.getenv("ACCESS_TTL_MIN", "15"))
    REFRESH_TTL: int = int(os.getenv("REFRESH_TTL_DAYS", "7"))
    ISSUER: str = os.getenv("JWT_ISSUER", "climb-api")

    @property
    def access_delta(self) -> timedelta:
        return timedelta(minutes=self.ACCESS_TTL)

    @property
    def refresh_delta(self) -> timedelta:
        return timedelta(days=self.REFRESH_TTL)


settings = Settings()
