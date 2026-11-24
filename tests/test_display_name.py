"""Leaderboard and season standings names: a missing first or last name used
to show as "None" (e.g. "Anna None")."""
import pytest

from schema.climber import display_name


@pytest.mark.parametrize(
    ("firstname", "lastname", "expected"),
    [
        ("Anna", "Berg", "Anna Berg"),
        ("Anna", None, "Anna"),
        (None, "Berg", "Berg"),
        ("Anna", "", "Anna"),
        (None, None, "anna98"),
        ("", "", "anna98"),
    ],
)
def test_display_name(firstname, lastname, expected):
    assert display_name(firstname, lastname, "anna98") == expected
