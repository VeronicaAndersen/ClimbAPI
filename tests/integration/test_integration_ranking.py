"""Integration tests: equal score totals share a rank."""
from datetime import date

from sqlalchemy.ext.asyncio import async_sessionmaker

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

LEVEL = 1
# Both pairs total 49.6, but as floats 25.0 + 24.6 != 24.9 + 24.7.
SCORES = {"a": (25.0, 24.6), "b": (24.9, 24.7)}


async def _seed(engine):
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        admin = Climber(username="admin", password="x", user_scope=UserScope.admin)
        climbers = {name: Climber(username=name, password="x") for name in SCORES}
        season = Season(name="S", year=2026)
        s.add_all([admin, season, *climbers.values()])
        await s.flush()
        comp = Competition(
            name="C", comp_type=CompType.QUALIFIER,
            comp_date=date(2026, 6, 15), season_id=season.id,
        )
        s.add(comp)
        await s.flush()
        problems = [
            Problem(competition_id=comp.id, level_no=LEVEL, problem_no=n) for n in (1, 2)
        ]
        s.add_all(problems)
        await s.flush()
        for name, climber in climbers.items():
            s.add(Registration(comp_id=comp.id, user_id=climber.id, level=LEVEL, approved=True))
            for problem, score in zip(problems, SCORES[name]):
                s.add(ProblemScore(
                    competition_id=comp.id, problem_id=problem.id, user_id=climber.id,
                    attempts_total=1, got_bonus=True, got_top=True,
                    attempts_to_bonus=1, attempts_to_top=1, ifsc_score=score,
                ))
        await s.commit()
        headers = {"Authorization": f"Bearer {create_access_token(admin.id)}"}
        return comp.id, season.id, headers


def _ranks(entries):
    return sorted((e["name"], e["rank"], e["total_score"]) for e in entries)


async def test_leaderboard_ranks_equal_totals_together(client, engine):
    comp_id, _, headers = await _seed(engine)

    resp = await client.get(f"/api/v1/competition/{comp_id}/leaderboard", headers=headers)

    assert resp.status_code == 200, resp.text
    entries = resp.json()["levels"][0]["entries"]
    assert _ranks(entries) == [("a", 1, 49.6), ("b", 1, 49.6)]


async def test_season_standings_rank_equal_totals_together(client, engine):
    _, season_id, headers = await _seed(engine)

    resp = await client.get(f"/api/v1/season/{season_id}/standings", headers=headers)

    assert resp.status_code == 200, resp.text
    entries = resp.json()["levels"][0]["entries"]
    assert _ranks(entries) == [("a", 1, 49.6), ("b", 1, 49.6)]
