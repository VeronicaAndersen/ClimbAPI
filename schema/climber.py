import unicodedata
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, constr, field_validator

from schema.auth import PASSWORD_MAX_LENGTH, USERNAME_MAX_LENGTH, TokenPair


def normalize_username(username: str) -> str:
    """Canonical form of a username: trimmed and lowercased, as stored."""
    return username.strip().lower()


def contains_control_characters(value: str) -> bool:
    """True if ``value`` has a control character (Unicode category Cc), e.g. NUL."""
    return any(unicodedata.category(char) == "Cc" for char in value)


# Control (Cc), format (Cf: zero-width characters, bidi overrides such as
# U+202E) and surrogate (Cs: lone surrogates, not storable as UTF-8)
# characters. Format characters let two usernames look identical or make one
# display as something else.
_FORBIDDEN_USERNAME_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})


def contains_forbidden_username_characters(value: str) -> bool:
    """True if ``value`` has a control, invisible format or surrogate character."""
    return any(
        unicodedata.category(char) in _FORBIDDEN_USERNAME_CATEGORIES for char in value
    )


def is_valid_normalized_username(username: str) -> bool:
    """
    Whether a normalized username could belong to an account: signup rejects
    the rest, so lookups can refuse them without touching the database.
    """
    return (
        bool(username)
        and len(username) <= USERNAME_MAX_LENGTH
        and not contains_forbidden_username_characters(username)
    )


def _normalized_username_within_limit(username: str) -> str:
    """
    Normalize and re-check. The raw-length checks are not enough: trimming
    can empty a string ("   "), and lowercasing can lengthen one ("İ" becomes
    two characters). Control characters such as NUL are refused too (the
    database rejects NUL in text, which used to surface as a 500), as are
    invisible format characters and lone surrogates.
    """
    normalized = normalize_username(username)
    if not normalized:
        raise ValueError("username must not be empty or only whitespace")
    if len(normalized) > USERNAME_MAX_LENGTH:
        raise ValueError(f"username must be at most {USERNAME_MAX_LENGTH} characters")
    if contains_forbidden_username_characters(normalized):
        raise ValueError(
            "username must not contain control or invisible formatting characters"
        )
    return normalized


class ClimberCreate(BaseModel):
    username: constr(min_length=1, max_length=USERNAME_MAX_LENGTH)
    password: constr(min_length=6, max_length=PASSWORD_MAX_LENGTH)
    email: Optional[str] = None
    firstname: str
    lastname: str
    club: Optional[str] = None

    @field_validator('username')
    @classmethod
    def trim_lowercase_username(cls, v: str) -> str:
        """Trim and lowercase username."""
        return _normalized_username_within_limit(v) if v else v

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
    username: Optional[constr(min_length=1, max_length=USERNAME_MAX_LENGTH)] = None
    password: Optional[constr(min_length=6, max_length=PASSWORD_MAX_LENGTH)] = None
    email: Optional[str] = None
    firstname: Optional[str] = None
    lastname: Optional[str] = None
    club: Optional[str] = None

    @field_validator('username')
    @classmethod
    def trim_lowercase_username(cls, v: Optional[str]) -> Optional[str]:
        """Trim and lowercase username."""
        return _normalized_username_within_limit(v) if v else v

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
