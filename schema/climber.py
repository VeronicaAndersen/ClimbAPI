from datetime import datetime
from typing import Optional

from pydantic import BaseModel, constr, field_validator

from schema.auth import TokenPair


class ClimberCreate(BaseModel):
    username: constr(min_length=1, max_length=200)
    password: constr(min_length=6, max_length=1024)
    email: Optional[str] = None
    firstname: str
    lastname: str
    club: Optional[str] = None

    @field_validator('username')
    @classmethod
    def trim_lowercase_username(cls, v: str) -> str:
        """Trim and lowercase username."""
        return v.strip().lower() if v else v

    @field_validator('firstname', 'lastname')
    @classmethod
    def trim_string_fields(cls, v: str) -> str:
        """Trim whitespace from string fields."""
        return v.strip() if v else v

    @field_validator('email')
    @classmethod
    def trim_email(cls, v: Optional[str]) -> Optional[str]:
        """Trim and lowercase email."""
        if v:
            return v.strip().lower()
        return v


class ClimberUpdate(BaseModel):
    username: Optional[constr(min_length=1, max_length=200)] = None
    password: Optional[constr(min_length=6, max_length=1024)] = None
    email: Optional[str] = None
    firstname: Optional[str] = None
    lastname: Optional[str] = None
    club: Optional[str] = None

    @field_validator('username')
    @classmethod
    def trim_lowercase_username(cls, v: Optional[str]) -> Optional[str]:
        """Trim and lowercase username."""
        return v.strip().lower() if v else v

    @field_validator('firstname', 'lastname')
    @classmethod
    def trim_string_fields(cls, v: Optional[str]) -> Optional[str]:
        """Trim whitespace from string fields."""
        return v.strip() if v else v

    @field_validator('email')
    @classmethod
    def trim_email(cls, v: Optional[str]) -> Optional[str]:
        """Trim and lowercase email."""
        if v:
            return v.strip().lower()
        return v


class AdminClimberUpdate(ClimberUpdate):
    user_scope: Optional[str] = None


class ClimberOut(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    firstname: Optional[str] = None
    lastname: Optional[str] = None
    club: Optional[str] = None
    user_scope: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AuthOut(TokenPair):
    climber: ClimberOut
