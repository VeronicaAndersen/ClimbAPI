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
