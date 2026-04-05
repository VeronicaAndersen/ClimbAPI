"""Unit tests for schema/season.py — SeasonCreate, SeasonUpdate, standings shapes."""
import pytest
from pydantic import ValidationError

from schema.season import (
    SeasonCreate,
    SeasonUpdate,
    SeasonStandingsEntry,
    LevelStandings,
    SeasonStandingsResponse,
)


class TestSeasonCreate:
    def test_valid(self):
        s = SeasonCreate(name="2026", year=2026)
        assert s.name == "2026"
        assert s.year == 2026

    def test_name_stored(self):
        s = SeasonCreate(name="Spring League", year=2025)
        assert s.name == "Spring League"

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            SeasonCreate(year=2026)

    def test_missing_year_raises(self):
        with pytest.raises(ValidationError):
            SeasonCreate(name="2026")

    def test_missing_both_raises(self):
        with pytest.raises(ValidationError):
            SeasonCreate()


class TestSeasonUpdate:
    def test_all_none_is_valid(self):
        s = SeasonUpdate()
        assert s.name is None
        assert s.year is None

    def test_name_only(self):
        s = SeasonUpdate(name="New name")
        assert s.name == "New name"
        assert s.year is None

    def test_year_only(self):
        s = SeasonUpdate(year=2027)
        assert s.year == 2027
        assert s.name is None

    def test_full_update(self):
        s = SeasonUpdate(name="Spring", year=2025)
        assert s.name == "Spring"
        assert s.year == 2025


class TestSeasonStandingsEntry:
    def test_valid(self):
        e = SeasonStandingsEntry(rank=1, name="Alice", total_score=42.5)
        assert e.rank == 1
        assert e.name == "Alice"
        assert e.total_score == 42.5

    def test_integer_score_accepted(self):
        e = SeasonStandingsEntry(rank=2, name="Bob", total_score=10)
        assert e.total_score == 10.0

    def test_zero_score(self):
        e = SeasonStandingsEntry(rank=5, name="Carol", total_score=0.0)
        assert e.total_score == 0.0

    def test_missing_rank_raises(self):
        with pytest.raises(ValidationError):
            SeasonStandingsEntry(name="Alice", total_score=10.0)

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            SeasonStandingsEntry(rank=1, total_score=10.0)

    def test_missing_score_raises(self):
        with pytest.raises(ValidationError):
            SeasonStandingsEntry(rank=1, name="Alice")


class TestLevelStandings:
    def _entry(self, rank=1, name="X", score=0.0):
        return SeasonStandingsEntry(rank=rank, name=name, total_score=score)

    def test_valid_with_entries(self):
        ls = LevelStandings(level=3, entries=[self._entry()])
        assert ls.level == 3
        assert len(ls.entries) == 1

    def test_empty_entries_allowed(self):
        ls = LevelStandings(level=1, entries=[])
        assert ls.entries == []

    def test_multiple_entries(self):
        entries = [self._entry(rank=i, name=f"P{i}") for i in range(1, 4)]
        ls = LevelStandings(level=2, entries=entries)
        assert len(ls.entries) == 3

    def test_missing_level_raises(self):
        with pytest.raises(ValidationError):
            LevelStandings(entries=[])


class TestSeasonStandingsResponse:
    def test_valid_empty_levels(self):
        resp = SeasonStandingsResponse(season_id=1, season_name="2026", levels=[])
        assert resp.season_id == 1
        assert resp.season_name == "2026"
        assert resp.levels == []

    def test_with_levels(self):
        entry = SeasonStandingsEntry(rank=1, name="Alice", total_score=50.0)
        level = LevelStandings(level=4, entries=[entry])
        resp = SeasonStandingsResponse(season_id=2, season_name="2025", levels=[level])
        assert len(resp.levels) == 1
        assert resp.levels[0].level == 4

    def test_missing_season_id_raises(self):
        with pytest.raises(ValidationError):
            SeasonStandingsResponse(season_name="2026", levels=[])

    def test_missing_season_name_raises(self):
        with pytest.raises(ValidationError):
            SeasonStandingsResponse(season_id=1, levels=[])
