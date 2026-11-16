import unicodedata
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, constr, field_validator

from schema.auth import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    USERNAME_MAX_LENGTH,
    TokenPair,
)


# Two username rules, on purpose:
#
# * New usernames (signup, and a username that is set or changed through a
#   profile update) follow a strict rule: normalize_new_username() then
#   validate_new_username(). NFKC folds look-alikes (fullwidth "ａdmin",
#   decomposed "josé") into one stored form, so the UNIQUE constraint rejects
#   them as duplicates, and an allowlist keeps out emoji, symbols, invisible
#   and control characters.
# * Lookups (login, /token, password-reset request) stay lenient:
#   normalize_username() (strip + lower, no NFKC) and
#   is_valid_normalized_username() (length, and no Cc/Cf/Cs). Existing
#   accounts were created before the strict rule and include spaces, "@",
#   non-NFKC forms and emoji; they must keep logging in.


def normalize_username(username: str) -> str:
    """Lenient lookup form (login, password reset): trimmed and lowercased."""
    return username.strip().lower()


def normalize_new_username(username: str) -> str:
    """Stored form of a new username: strip, NFKC, then lowercase."""
    return unicodedata.normalize("NFKC", username.strip()).lower()


# Unicode Default_Ignorable_Code_Point (DerivedCoreProperties.txt). Most are
# Cf or unassigned, but some are letters or marks (Hangul fillers are Lo,
# variation selectors are Mn), so they need their own check.
_DEFAULT_IGNORABLE_RANGES = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160),
    (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
    (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
_NEW_USERNAME_PUNCTUATION = frozenset("._-@")
# Enough for real text (Vietnamese uses up to two on one letter, and NFKC
# composes most of them into one character); more is only good for
# "zalgo" names that render as a tall smear over neighbouring text.
_MAX_MARKS_PER_LETTER = 3
NEW_USERNAME_RULE = (
    "username may only contain letters (with accents), digits, single spaces "
    "between words, and . _ - @"
)


def _is_default_ignorable(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _DEFAULT_IGNORABLE_RANGES)


def validate_new_username(username: str) -> str:
    """
    Normalize a new username and check it against the strict rule.

    Returns the stored form, or raises ValueError with a message for the
    client. Allowed after normalizing: letters (L*), up to three combining
    marks (M*) in a row directly after a letter, decimal digits (Nd), the
    characters . _ - @, and single ASCII spaces between other characters.
    Everything else is refused, as are Default_Ignorable code points.
    """
    normalized = normalize_new_username(username)
    if not normalized:
        raise ValueError("username must not be empty or only whitespace")
    if len(normalized) > USERNAME_MAX_LENGTH:
        raise ValueError(f"username must be at most {USERNAME_MAX_LENGTH} characters")
    if normalized[0] == " " or normalized[-1] == " " or "  " in normalized:
        raise ValueError("username must not start or end with a space or contain double spaces")
    after_letter = False  # whether a combining mark is allowed here
    marks_in_row = 0
    for char in normalized:
        category = unicodedata.category(char)
        if _is_default_ignorable(char):
            raise ValueError(NEW_USERNAME_RULE)
        if category.startswith("L"):
            after_letter = True
            marks_in_row = 0
        elif category.startswith("M"):
            marks_in_row += 1
            if not after_letter:
                raise ValueError(NEW_USERNAME_RULE)
            if marks_in_row > _MAX_MARKS_PER_LETTER:
                raise ValueError(
                    f"username may have at most {_MAX_MARKS_PER_LETTER} accent marks on one letter"
                )
        elif category == "Nd" or char == " " or char in _NEW_USERNAME_PUNCTUATION:
            after_letter = False
        else:
            raise ValueError(NEW_USERNAME_RULE)
    return normalized


def resolve_username_change(requested: str, current: str) -> str | None:
    """
    The username to store for a profile update, or None if it is unchanged.

    A client may resend the account's current username, which can be a
    legacy one that the strict rule would refuse. That is not a change, so it
    is compared leniently first and never rejected. Only a real change is
    validated as a new username (ValueError if it fails), from the raw input
    exactly as at signup, so both store the same form.
    """
    if normalize_username(requested) == current:
        return None
    new_username = validate_new_username(requested)
    return None if new_username == current else new_username


def contains_control_characters(value: str) -> bool:
    """True if ``value`` has a control character (Unicode category Cc), e.g. NUL."""
    return any(unicodedata.category(char) == "Cc" for char in value)


def contains_unstorable_characters(value: str) -> bool:
    """
    True if ``value`` has a control character (Cc, e.g. NUL) or a lone
    surrogate (Cs). The database rejects NUL in text, and a lone surrogate
    cannot be encoded as UTF-8; both used to surface as a 500.
    """
    return any(unicodedata.category(char) in {"Cc", "Cs"} for char in value)


# Bidi embedding/override (U+202A-U+202E) and isolate (U+2066-U+2069)
# controls: they can make text display in a different order than stored.
_BIDI_CONTROLS = frozenset(map(chr, [*range(0x202A, 0x202F), *range(0x2066, 0x206A)]))


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


def _plain_text(value: str | None) -> str | None:
    """
    For email and club: no control characters, lone surrogates or invisible
    format characters (Cf), none of which belong in an address or club name.
    """
    if value is not None and (
        contains_unstorable_characters(value)
        or any(unicodedata.category(char) == "Cf" for char in value)
    ):
        raise ValueError("must not contain control or invisible formatting characters")
    return value


def _person_name(value: str | None) -> str | None:
    """
    For first and last names: no control characters or lone surrogates, and
    no bidi controls. Other format characters stay allowed, since zero-width
    joiners (emoji sequences) and non-joiners are legitimate in names.
    """
    if value is not None and (
        contains_unstorable_characters(value)
        or any(char in _BIDI_CONTROLS for char in value)
    ):
        raise ValueError("must not contain control or bidirectional override characters")
    return value


class ClimberCreate(BaseModel):
    username: constr(min_length=1, max_length=USERNAME_MAX_LENGTH)
    password: constr(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    email: Optional[str] = None
    firstname: str
    lastname: str
    club: Optional[str] = None

    @field_validator('username')
    @classmethod
    def new_username(cls, v: str) -> str:
        """A new account gets a username that follows the strict rule."""
        return validate_new_username(v)

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

    @field_validator('email', 'club')
    @classmethod
    def reject_hidden_characters(cls, v: str | None) -> str | None:
        return _plain_text(v)

    @field_validator('firstname', 'lastname')
    @classmethod
    def reject_hidden_name_characters(cls, v: str | None) -> str | None:
        return _person_name(v)


class ClimberUpdate(BaseModel):
    username: Optional[constr(min_length=1, max_length=USERNAME_MAX_LENGTH)] = None
    password: Optional[constr(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)] = None
    email: Optional[str] = None
    firstname: Optional[str] = None
    lastname: Optional[str] = None
    club: Optional[str] = None

    @field_validator('username')
    @classmethod
    def lenient_username(cls, v: str | None) -> str | None:
        """
        Only the lenient checks here (length, no Cc/Cf/Cs); the value is
        returned trimmed but otherwise as typed. Whether the username actually
        changes depends on the stored one, so the endpoint applies the strict
        rule through resolve_username_change(). That function must see the
        raw input: lowercasing before NFKC can give a different result than
        signup does (U+03F9 becomes "s" via final sigma instead of sigma).
        """
        if v:
            _normalized_username_within_limit(v)
            return v.strip()
        return v

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

    @field_validator('email', 'club')
    @classmethod
    def reject_hidden_characters(cls, v: str | None) -> str | None:
        return _plain_text(v)

    @field_validator('firstname', 'lastname')
    @classmethod
    def reject_hidden_name_characters(cls, v: str | None) -> str | None:
        return _person_name(v)


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
