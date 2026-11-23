"""Integration tests for problem score edit window and zero-filled GETs."""
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import api.v1.problem_score as ps_module
from db.models import (
    Climber,
    Competition,
    CompType,
    Problem,
    ProblemScore,
    Registration,
    Season,
    UserScope,
)
from security.jwt_tools import create_access_token

BASE = "/api/v1/competitions"
TODAY = date(2026, 6, 15)
LEVEL = 1
REAL_COMPETITION_TODAY = ps_module.competition_today


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(ps_module, "competition_today", lambda: TODAY)


async def _seed(engine, comp_date, scope=UserScope.climber, with_score_for_1=False):
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        user = Climber(username="u1", password="x", user_scope=scope)
        season = Season(name="S", year=2026)
        s.add_all([user, season])
        await s.flush()
        comp = Competition(
            name="C", comp_type=CompType.QUALIFIER,
            comp_date=comp_date, season_id=season.id,
        )
        s.add(comp)
        await s.flush()
        s.add(Registration(comp_id=comp.id, user_id=user.id, level=LEVEL))
        problems = [
            Problem(competition_id=comp.id, level_no=LEVEL, problem_no=n)
            for n in (1, 2, 3)
        ]
        s.add_all(problems)
        await s.flush()
        if with_score_for_1:
            s.add(ProblemScore(
                competition_id=comp.id, problem_id=problems[0].id,
                user_id=user.id, attempts_total=2, got_bonus=True,
                got_top=True, attempts_to_bonus=1, attempts_to_top=2,
                ifsc_score=24.9,
            ))
        await s.commit()
        headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
        return comp.id, headers


BODY = {
    "attempts_total": 2, "got_bonus": True, "got_top": True,
    "attempts_to_bonus": 1, "attempts_to_top": 2,
}


def _single(comp_id):
    return f"{BASE}/{comp_id}/level/{LEVEL}/problems/1/score"


def _batch(comp_id):
    return f"{BASE}/{comp_id}/level/{LEVEL}/scores/batch"


class TestEditWindow:
    async def test_climber_rejected_on_other_day_single(self, client, engine):
        cid, h = await _seed(engine, TODAY - timedelta(days=1))
        r = await client.put(_single(cid), json=BODY, headers=h)
        assert r.status_code == 403
        assert "competition day" in r.json()["detail"]

    async def test_climber_rejected_on_other_day_batch(self, client, engine):
        cid, h = await _seed(engine, TODAY + timedelta(days=1))
        r = await client.put(_batch(cid), json={"items": [{**BODY, "problem_no": 1}]}, headers=h)
        assert r.status_code == 403

    async def test_climber_allowed_on_comp_day(self, client, engine):
        cid, h = await _seed(engine, TODAY)
        r = await client.put(_single(cid), json=BODY, headers=h)
        assert r.status_code == 200
        assert r.json()["ifsc_score"] == pytest.approx(24.9)
        r = await client.put(_batch(cid), json={"items": [{**BODY, "problem_no": 2}]}, headers=h)
        assert r.status_code == 200

    async def test_admin_allowed_any_day(self, client, engine):
        cid, h = await _seed(engine, TODAY - timedelta(days=30), scope=UserScope.admin)
        assert (await client.put(_single(cid), json=BODY, headers=h)).status_code == 200
        r = await client.put(_batch(cid), json={"items": [{**BODY, "problem_no": 2}]}, headers=h)
        assert r.status_code == 200

    async def test_missing_competition_404(self, client, engine):
        _, h = await _seed(engine, TODAY)
        assert (await client.put(_single(9999), json=BODY, headers=h)).status_code == 404
        r = await client.put(_batch(9999), json={"items": [{**BODY, "problem_no": 1}]}, headers=h)
        assert r.status_code == 404


class TestGetZeroFill:
    async def test_returns_all_problems_when_only_some_have_rows(self, client, engine):
        cid, h = await _seed(engine, TODAY, with_score_for_1=True)
        r = await client.get(_batch(cid), headers=h)
        assert r.status_code == 200
        data = r.json()
        assert [d["problem_no"] for d in data] == [1, 2, 3]
        assert data[0]["score"]["got_top"] is True
        assert data[1]["score"]["attempts_total"] == 0
        assert data[2]["score"]["ifsc_score"] == 0.0

    async def test_admin_user_scores_returns_all_problems(self, client, engine):
        cid, h = await _seed(engine, TODAY, scope=UserScope.admin, with_score_for_1=True)
        r = await client.get(f"{BASE}/{cid}/level/{LEVEL}/users/1/scores", headers=h)
        assert r.status_code == 200
        assert [d["problem_no"] for d in r.json()] == [1, 2, 3]


class TestCompetitionToday:
    def test_uses_stockholm_date_not_utc(self, monkeypatch):
        # 23:30 UTC on 14 June is already 15 June in Stockholm (UTC+2).
        fixed = datetime(2026, 6, 14, 23, 30, tzinfo=UTC)

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz)

        monkeypatch.setattr(ps_module, "datetime", FixedDatetime)
        assert REAL_COMPETITION_TODAY() == date(2026, 6, 15)
