"""Unit tests for schema/climber.py validators."""
import pytest
from pydantic import ValidationError
from schema.climber import ClimberCreate, ClimberUpdate, AdminClimberUpdate


class TestClimberCreate:
    def test_username_is_lowercased(self):
        c = ClimberCreate(username="JohnDoe", password="secret123", firstname="John", lastname="Doe")
        assert c.username == "johndoe"

    def test_username_is_trimmed(self):
        c = ClimberCreate(username="  alice  ", password="secret123", firstname="Alice", lastname="Smith")
        assert c.username == "alice"

    def test_username_trimmed_and_lowercased(self):
        c = ClimberCreate(username="  ADMIN_USER  ", password="secret123", firstname="A", lastname="B")
        assert c.username == "admin_user"

    def test_email_is_lowercased(self):
        c = ClimberCreate(username="alice", password="secret123", firstname="Alice", lastname="Smith", email="ALICE@EXAMPLE.COM")
        assert c.email == "alice@example.com"

    def test_email_is_trimmed(self):
        c = ClimberCreate(username="alice", password="secret123", firstname="Alice", lastname="Smith", email="  alice@example.com  ")
        assert c.email == "alice@example.com"

    def test_firstname_is_trimmed(self):
        c = ClimberCreate(username="alice", password="secret123", firstname="  Alice  ", lastname="Smith")
        assert c.firstname == "Alice"

    def test_lastname_is_trimmed(self):
        c = ClimberCreate(username="alice", password="secret123", firstname="Alice", lastname="  Smith  ")
        assert c.lastname == "Smith"

    def test_password_too_short_raises(self):
        with pytest.raises(ValidationError):
            ClimberCreate(username="alice", password="abc", firstname="Alice", lastname="Smith")

    def test_username_empty_raises(self):
        with pytest.raises(ValidationError):
            ClimberCreate(username="", password="secret123", firstname="Alice", lastname="Smith")

    def test_club_is_optional(self):
        c = ClimberCreate(username="alice", password="secret123", firstname="Alice", lastname="Smith")
        assert c.club is None

    def test_email_is_optional(self):
        c = ClimberCreate(username="alice", password="secret123", firstname="Alice", lastname="Smith")
        assert c.email is None

    def test_club_stored_as_given(self):
        c = ClimberCreate(username="alice", password="secret123", firstname="Alice", lastname="Smith", club="Klätterklubb")
        assert c.club == "Klätterklubb"


class TestClimberUpdate:
    def test_all_fields_optional(self):
        c = ClimberUpdate()
        assert c.username is None
        assert c.password is None
        assert c.email is None
        assert c.firstname is None
        assert c.lastname is None
        assert c.club is None

    def test_username_lowercased_when_provided(self):
        c = ClimberUpdate(username="JohnDoe")
        assert c.username == "johndoe"

    def test_email_normalized_when_provided(self):
        c = ClimberUpdate(email="  USER@EXAMPLE.COM  ")
        assert c.email == "user@example.com"

    def test_password_too_short_raises(self):
        with pytest.raises(ValidationError):
            ClimberUpdate(password="abc")

    def test_partial_update_only_username(self):
        c = ClimberUpdate(username="newname")
        assert c.username == "newname"
        assert c.password is None
        assert c.email is None

    def test_club_can_be_cleared_with_empty_string(self):
        c = ClimberUpdate(club="")
        assert c.club == ""


class TestAdminClimberUpdate:
    def test_includes_user_scope(self):
        c = AdminClimberUpdate(user_scope="admin")
        assert c.user_scope == "admin"

    def test_user_scope_optional(self):
        c = AdminClimberUpdate()
        assert c.user_scope is None

    def test_inherits_username_normalization(self):
        c = AdminClimberUpdate(username="  ADMIN  ", user_scope="admin")
        assert c.username == "admin"

    def test_can_set_climber_scope(self):
        c = AdminClimberUpdate(user_scope="climber")
        assert c.user_scope == "climber"


@pytest.mark.parametrize("model", [ClimberCreate, ClimberUpdate])
def test_username_length_is_checked_after_lowercasing(model):
    with pytest.raises(ValidationError):
        model(username="İ" * 200, password="secret123", firstname="A", lastname="B")


@pytest.mark.parametrize("model", [ClimberCreate, ClimberUpdate])
@pytest.mark.parametrize("username", [" ", "   ", "\t\n"])
def test_whitespace_only_username_is_rejected(model, username):
    """Regression: it passed min_length=1 and was stored as an empty username."""
    with pytest.raises(ValidationError):
        model(username=username, password="secret123", firstname="A", lastname="B")


@pytest.mark.parametrize("model", [ClimberCreate, ClimberUpdate])
@pytest.mark.parametrize("username", ["ali\x00ce", "\x00", "a\x1fb", "a\x7fb", "a\tb", "a\x85b"])
def test_username_with_control_characters_is_rejected(model, username):
    """Regression: NUL reached the database and surfaced as a 500."""
    with pytest.raises(ValidationError):
        model(username=username, password="secret123", firstname="A", lastname="B")


def test_username_with_ordinary_unicode_is_accepted():
    c = ClimberCreate(username="Åsa-Öberg_ü", password="secret123", firstname="A", lastname="B")
    assert c.username == "åsa-öberg_ü"


@pytest.mark.parametrize("model", [ClimberCreate, ClimberUpdate])
@pytest.mark.parametrize(
    "username",
    ["ad\u200bmin", "\u202eadmin", "ad\u200dmin", "\ufeffadmin", "ad\u2066min", "a\ud800b"],
)
def test_username_with_format_or_surrogate_characters_is_rejected(model, username):
    """Regression: zero-width and bidi characters allowed look-alike usernames."""
    with pytest.raises(ValidationError):
        model(username=username, password="secret123", firstname="A", lastname="B")


@pytest.mark.parametrize("model", [ClimberCreate, ClimberUpdate])
@pytest.mark.parametrize("field", ["email", "firstname", "lastname", "club"])
@pytest.mark.parametrize("value", ["a\x00b", "a\x1bb"])
def test_text_fields_reject_control_characters(model, field, value):
    """Regression: NUL in these fields reached the database and gave a 500."""
    data = {"username": "alice", "password": "secret123", "firstname": "A", "lastname": "B"}
    data[field] = value
    with pytest.raises(ValidationError):
        model(**data)


def test_text_fields_accept_ordinary_text():
    c = ClimberCreate(
        username="alice",
        password="secret123",
        firstname="Åsa",
        lastname="Öberg-Lind",
        club="Klätterklubben 08",
        email="Alice@Example.com",
    )
    assert (c.firstname, c.club, c.email) == ("Åsa", "Klätterklubben 08", "alice@example.com")


def _climber(model, **fields):
    data = {"username": "alice", "password": "secret123", "firstname": "A", "lastname": "B"}
    data.update(fields)
    return model(**data)


@pytest.mark.parametrize("model", [ClimberCreate, ClimberUpdate])
@pytest.mark.parametrize("field", ["email", "firstname", "lastname", "club"])
def test_text_fields_reject_lone_surrogates(model, field):
    """Regression: a lone surrogate reached the database and gave a 500."""
    with pytest.raises(ValidationError):
        _climber(model, **{field: "A\ud800"})


@pytest.mark.parametrize("model", [ClimberCreate, ClimberUpdate])
@pytest.mark.parametrize("field", ["email", "club"])
@pytest.mark.parametrize("char", ["\u200b", "\u200d", "\u202e", "\ufeff", "\u2066"])
def test_email_and_club_reject_format_characters(model, field, char):
    with pytest.raises(ValidationError):
        _climber(model, **{field: f"ab{char}c"})


@pytest.mark.parametrize("model", [ClimberCreate, ClimberUpdate])
@pytest.mark.parametrize("field", ["firstname", "lastname"])
@pytest.mark.parametrize(
    "char", ["\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069"]
)
def test_names_reject_bidi_controls(model, field, char):
    with pytest.raises(ValidationError):
        _climber(model, **{field: f"An{char}na"})


@pytest.mark.parametrize("model", [ClimberCreate, ClimberUpdate])
@pytest.mark.parametrize("name", ["Klättrare \U0001F469\u200d\U0001F4BB", "Ann\u200cika", "Liv\u200b"])
def test_names_keep_other_format_characters(model, name):
    """Zero-width joiners (emoji sequences) and non-joiners are legitimate in names."""
    climber = _climber(model, firstname=name, lastname=name)
    assert climber.firstname == name.strip()
