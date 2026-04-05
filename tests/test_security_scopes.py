"""Unit tests for security/deps.py — scope expansion and role hierarchy."""
from security.deps import expand_scopes, ROLE_HIERARCHY, ALL_SCOPES


class _Scope:
    """Minimal stand-in for the UserScope enum — just needs a .value attribute."""
    def __init__(self, value: str):
        self.value = value


class TestRoleHierarchy:
    def test_all_scopes_present(self):
        assert ALL_SCOPES == {"climber", "setter", "analyst", "admin"}

    def test_climber_only_has_climber(self):
        assert ROLE_HIERARCHY["climber"] == {"climber"}

    def test_setter_includes_climber(self):
        assert "climber" in ROLE_HIERARCHY["setter"]
        assert "setter" in ROLE_HIERARCHY["setter"]

    def test_analyst_includes_climber(self):
        assert "climber" in ROLE_HIERARCHY["analyst"]
        assert "analyst" in ROLE_HIERARCHY["analyst"]

    def test_admin_includes_all(self):
        assert ROLE_HIERARCHY["admin"] == {"admin", "analyst", "setter", "climber"}

    def test_no_role_grants_more_than_admin(self):
        for scopes in ROLE_HIERARCHY.values():
            assert scopes.issubset(ALL_SCOPES)


class TestExpandScopes:
    def test_climber_scope(self):
        assert expand_scopes(_Scope("climber")) == {"climber"}

    def test_setter_scope(self):
        result = expand_scopes(_Scope("setter"))
        assert "setter" in result
        assert "climber" in result

    def test_analyst_scope(self):
        result = expand_scopes(_Scope("analyst"))
        assert "analyst" in result
        assert "climber" in result

    def test_admin_scope(self):
        result = expand_scopes(_Scope("admin"))
        assert result == {"admin", "analyst", "setter", "climber"}

    def test_unknown_role_returns_empty(self):
        assert expand_scopes(_Scope("unknown")) == set()

    def test_uppercase_role_is_normalised(self):
        """The function lowercases the role value before lookup."""
        assert expand_scopes(_Scope("ADMIN")) == {"admin", "analyst", "setter", "climber"}

    def test_mixed_case_role_is_normalised(self):
        assert expand_scopes(_Scope("Climber")) == {"climber"}
