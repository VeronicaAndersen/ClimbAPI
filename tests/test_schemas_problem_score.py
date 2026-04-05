"""Unit tests for schema/problem_score.py — ProblemScoreUpsert validation rules."""
import pytest
from pydantic import ValidationError
from schema.problem_score import ProblemScoreUpsert, ProblemScoreBulkItem, ProblemScoreBulkRequest


def valid_score(**overrides):
    """Return a minimal valid score payload, optionally overriding fields."""
    base = {
        "attempts_total": 3,
        "got_bonus": False,
        "got_top": False,
        "attempts_to_bonus": None,
        "attempts_to_top": None,
    }
    return {**base, **overrides}


class TestProblemScoreUpsertBasic:
    def test_valid_no_bonus_no_top(self):
        s = ProblemScoreUpsert(**valid_score())
        assert s.got_bonus is False
        assert s.got_top is False

    def test_valid_bonus_only(self):
        s = ProblemScoreUpsert(**valid_score(got_bonus=True, attempts_to_bonus=2))
        assert s.got_bonus is True
        assert s.attempts_to_bonus == 2

    def test_valid_bonus_and_top(self):
        s = ProblemScoreUpsert(**valid_score(
            got_bonus=True, attempts_to_bonus=2,
            got_top=True, attempts_to_top=3,
        ))
        assert s.got_bonus is True
        assert s.got_top is True

    def test_attempts_total_zero_is_valid(self):
        s = ProblemScoreUpsert(**valid_score(attempts_total=0))
        assert s.attempts_total == 0

    def test_negative_attempts_total_raises(self):
        with pytest.raises(ValidationError):
            ProblemScoreUpsert(**valid_score(attempts_total=-1))


class TestProblemScoreUpsertRules:
    def test_got_bonus_without_attempts_to_bonus_raises(self):
        with pytest.raises(ValidationError, match="attempts_to_bonus is required"):
            ProblemScoreUpsert(**valid_score(got_bonus=True, attempts_to_bonus=None))

    def test_got_top_without_attempts_to_top_raises(self):
        with pytest.raises(ValidationError, match="attempts_to_top is required"):
            ProblemScoreUpsert(**valid_score(got_top=True, attempts_to_top=None))

    def test_attempts_to_bonus_exceeds_total_raises(self):
        with pytest.raises(ValidationError, match="attempts_to_bonus cannot exceed attempts_total"):
            ProblemScoreUpsert(**valid_score(
                attempts_total=2,
                got_bonus=True, attempts_to_bonus=5,
            ))

    def test_attempts_to_top_exceeds_total_raises(self):
        with pytest.raises(ValidationError, match="attempts_to_top cannot exceed attempts_total"):
            ProblemScoreUpsert(**valid_score(
                attempts_total=2,
                got_top=True, attempts_to_top=5,
            ))

    def test_attempts_to_top_less_than_bonus_raises(self):
        """Top must not precede bonus (IFSC rule)."""
        with pytest.raises(ValidationError, match="attempts_to_top must be >= attempts_to_bonus"):
            ProblemScoreUpsert(**valid_score(
                attempts_total=5,
                got_bonus=True, attempts_to_bonus=4,
                got_top=True, attempts_to_top=2,
            ))

    def test_attempts_to_top_equal_to_bonus_is_valid(self):
        """Top on the same attempt as bonus is valid."""
        s = ProblemScoreUpsert(**valid_score(
            attempts_total=5,
            got_bonus=True, attempts_to_bonus=3,
            got_top=True, attempts_to_top=3,
        ))
        assert s.attempts_to_top == 3

    def test_top_without_bonus_is_allowed(self):
        """Top can be achieved independently of bonus."""
        s = ProblemScoreUpsert(**valid_score(
            got_bonus=False,
            got_top=True, attempts_to_top=2,
        ))
        assert s.got_top is True
        assert s.got_bonus is False


class TestProblemScoreBulkItem:
    def test_valid_problem_no(self):
        item = ProblemScoreBulkItem(**valid_score(problem_no=1))
        assert item.problem_no == 1

    def test_problem_no_max_is_8(self):
        item = ProblemScoreBulkItem(**valid_score(problem_no=8))
        assert item.problem_no == 8

    def test_problem_no_zero_raises(self):
        with pytest.raises(ValidationError):
            ProblemScoreBulkItem(**valid_score(problem_no=0))

    def test_problem_no_above_8_raises(self):
        with pytest.raises(ValidationError):
            ProblemScoreBulkItem(**valid_score(problem_no=9))


class TestProblemScoreBulkRequest:
    def _item(self, problem_no: int):
        return {**valid_score(), "problem_no": problem_no}

    def test_valid_single_item(self):
        req = ProblemScoreBulkRequest(items=[self._item(1)])
        assert len(req.items) == 1

    def test_valid_multiple_unique_items(self):
        req = ProblemScoreBulkRequest(items=[self._item(i) for i in range(1, 5)])
        assert len(req.items) == 4

    def test_empty_items_raises(self):
        with pytest.raises(ValidationError):
            ProblemScoreBulkRequest(items=[])

    def test_duplicate_problem_no_raises(self):
        with pytest.raises(ValidationError, match="Duplicate problem_no"):
            ProblemScoreBulkRequest(items=[self._item(1), self._item(1)])

    def test_more_than_8_items_raises(self):
        with pytest.raises(ValidationError):
            ProblemScoreBulkRequest(items=[self._item(i) for i in range(1, 10)])
