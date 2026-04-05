"""Unit tests for schema/competition.py — CompetitionCreate cross-field validation."""
import pytest
from datetime import date
from pydantic import ValidationError
from schema.competition import CompetitionCreate


def make_competition(**overrides):
    base = {
        "name": "Test Competition",
        "comp_type": "QUALIFIER",
        "comp_date": date(2026, 6, 1),
        "season_id": 1,
        "round_no": 1,
    }
    return {**base, **overrides}


class TestCompetitionCreateQualifier:
    def test_valid_qualifier_with_round_no(self):
        c = CompetitionCreate(**make_competition(comp_type="QUALIFIER", round_no=1))
        assert c.comp_type.value == "QUALIFIER"
        assert c.round_no == 1

    def test_qualifier_without_round_no_raises(self):
        with pytest.raises(ValidationError, match="Qualifier must have round_no"):
            CompetitionCreate(**make_competition(comp_type="QUALIFIER", round_no=None))

    def test_qualifier_round_no_1_to_4_valid(self):
        for rn in range(1, 5):
            c = CompetitionCreate(**make_competition(comp_type="QUALIFIER", round_no=rn))
            assert c.round_no == rn

    def test_qualifier_round_no_zero_raises(self):
        with pytest.raises(ValidationError):
            CompetitionCreate(**make_competition(comp_type="QUALIFIER", round_no=0))

    def test_qualifier_round_no_above_4_raises(self):
        with pytest.raises(ValidationError):
            CompetitionCreate(**make_competition(comp_type="QUALIFIER", round_no=5))


class TestCompetitionCreateFinal:
    def test_valid_final_without_round_no(self):
        c = CompetitionCreate(**make_competition(comp_type="FINAL", round_no=None))
        assert c.comp_type.value == "FINAL"
        assert c.round_no is None

    def test_final_with_round_no_raises(self):
        with pytest.raises(ValidationError, match="Final must not have round_no"):
            CompetitionCreate(**make_competition(comp_type="FINAL", round_no=1))


class TestCompetitionCreateFields:
    def test_description_is_optional(self):
        c = CompetitionCreate(**make_competition())
        assert c.description is None

    def test_description_stored(self):
        c = CompetitionCreate(**make_competition(description="Annual championship"))
        assert c.description == "Annual championship"

    def test_invalid_comp_type_raises(self):
        with pytest.raises(ValidationError):
            CompetitionCreate(**make_competition(comp_type="INVALID"))
