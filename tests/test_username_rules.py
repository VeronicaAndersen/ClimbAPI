"""Unit tests for the new-username rule and the lenient lookup rule."""

import pytest
from pydantic import ValidationError

from schema.climber import (
    ClimberCreate,
    is_valid_normalized_username,
    normalize_new_username,
    normalize_username,
    resolve_username_change,
    validate_new_username,
)

# ---------------------------------------------------------------------------
# validate_new_username: allowed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("\u00c5sa \u00d6berg", "\u00e5sa \u00f6berg"),
        ("bj\u00f6rn.str\u00f6m", "bj\u00f6rn.str\u00f6m"),
        ("Jos\u00e9", "jos\u00e9"),
        ("s\u00f8ren_\u00f8", "s\u00f8ren_\u00f8"),
        ("anna svensson", "anna svensson"),
        ("anna@example.se", "anna@example.se"),
        ("climber_2024", "climber_2024"),
        ("a.b_c-d@e", "a.b_c-d@e"),
        ("  Trimmed  ", "trimmed"),
        ("7", "7"),
        ("x" * 200, "x" * 200),
        # NBSP, the ideographic space and the thin space are spaces under
        # NFKC, so a single one between words is stored as a plain space.
        ("anna\u00a0svensson", "anna svensson"),
        ("anna\u3000svensson", "anna svensson"),
        ("anna\u2009svensson", "anna svensson"),
        # Fullwidth letters and decomposed accents are folded by NFKC.
        ("\uff41dmin", "admin"),
        ("jose\u0301", "jos\u00e9"),
        # Combining marks directly after a letter.
        ("e\u0301e\u0301", "\u00e9\u00e9"),
        ("a\u0301\u0302", "\u00e1\u0302"),
        # Mixed scripts are allowed for now (follow-up: confusables).
        ("p\u0430ypal", "p\u0430ypal"),  # Cyrillic "a"
    ],
)
def test_new_username_allowed(raw, stored):
    assert validate_new_username(raw) == stored


def test_nfkc_then_lowercase_order():
    # U+210C BLACK-LETTER CAPITAL H: NFKC gives "H", lowercasing must follow.
    assert normalize_new_username("\u210cello") == "hello"


# ---------------------------------------------------------------------------
# validate_new_username: rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "anna \U0001f338",  # emoji (So)
        "anna\U0001f338",
        "anna  svensson",  # double space
        "anna\u00a0\u00a0svensson",  # becomes a double space
        "anna\u3000\u3000svensson",
        "ad\u200bmin",  # zero-width space (Cf)
        "\u202eadmin",  # bidi override (Cf)
        "anna\nsvensson",  # line break (Cc)
        "anna\tsvensson",
        "anna\u2028svensson",  # line separator (Zl)
        "a+b",  # math symbol (Sm)
        "a!b",  # other punctuation (Po)
        "a,b",
        "a/b",
        "a#b",
        "a$b",  # currency (Sc)
        "\u0301abc",  # combining mark not after a letter
        "1\u0301",
        "a.\u0301",
        "a\ufe0f",  # variation selector: Mn but Default_Ignorable
        "a\u3164b",  # Hangul filler: Lo but Default_Ignorable
        "a\u00adb",  # soft hyphen
        "a\ue000b",  # private use (Co)
        "a\U000e0001b",  # language tag
        "x" * 201,
        "\ufb03" * 100,  # the ffi ligature becomes "ffi": 300 characters
    ],
)
def test_new_username_rejected(raw):
    with pytest.raises(ValueError):
        validate_new_username(raw)


def test_rejection_message_is_clear():
    with pytest.raises(ValueError, match=r"letters .*digits.*single spaces.*\. _ - @"):
        validate_new_username("anna \U0001f338")


def test_signup_schema_uses_strict_rule():
    with pytest.raises(ValidationError):
        ClimberCreate(
            username="anna \U0001f338", password="secret123", firstname="A", lastname="B"
        )
    created = ClimberCreate(
        username="\uff21\uff2e\uff2e\uff21 Svensson",
        password="secret123",
        firstname="A",
        lastname="B",
    )
    assert created.username == "anna svensson"


# ---------------------------------------------------------------------------
# Lookups stay lenient
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored",
    ["anna \U0001f338", "anna svensson", "anna@example.se", "\uff41dmin", "jose\u0301", "a  b"],
)
def test_lenient_lookup_accepts_legacy_usernames(stored):
    assert is_valid_normalized_username(normalize_username(stored))
    assert normalize_username(f"  {stored.upper()} ") == stored.lower()


# ---------------------------------------------------------------------------
# resolve_username_change: only a real change is validated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "requested", ["anna \U0001f338", "  ANNA \U0001f338 ", "Anna \U0001f338"]
)
def test_resending_legacy_username_is_not_a_change(requested):
    assert resolve_username_change(requested, "anna \U0001f338") is None


def test_change_to_normalized_form_of_current_is_not_a_change():
    assert resolve_username_change("\uff2a\uff2f\uff33\uff25\u0301", "jos\u00e9") is None


def test_real_change_is_validated():
    assert resolve_username_change("anna.s", "anna \U0001f338") == "anna.s"
    with pytest.raises(ValueError):
        resolve_username_change("anna \U0001f33a", "anna \U0001f338")



# ---------------------------------------------------------------------------
# Stacked combining marks: at most three on one letter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        # Vietnamese, typed decomposed: NFKC composes it.
        ("e\u0323\u0302", "\u1ec7"),
        ("Nguye\u0302\u0303n", "nguy\u1ec5n"),
        # Marks with no precomposed form stay separate: three are allowed.
        ("x\u0301\u0302\u0303", "x\u0301\u0302\u0303"),
        ("a\u0301\u0301\u0301", "\u00e1\u0301\u0301"),
    ],
)
def test_up_to_three_marks_per_letter_allowed(raw, stored):
    assert validate_new_username(raw) == stored


@pytest.mark.parametrize(
    "raw",
    [
        "x\u0301\u0302\u0303\u0304",  # four marks on one letter
        "a" + "\u0301" * 5,  # one composes, four stay
        "ab" + "\u0300" * 4 + "c",
        "a" + "\u0301" * 199,  # the old rule allowed these up to 200 characters
    ],
)
def test_more_than_three_marks_per_letter_rejected(raw):
    """Regression: stacked marks were unlimited ("zalgo" names)."""
    with pytest.raises(ValueError, match="at most 3 accent marks"):
        validate_new_username(raw)


def test_mark_count_restarts_at_each_letter():
    assert validate_new_username("x\u0301\u0302\u0303y\u0301\u0302\u0303")
