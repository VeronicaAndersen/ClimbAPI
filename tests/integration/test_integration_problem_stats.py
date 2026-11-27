"""Integration tests: level problem stats count only approved climbers."""
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


async def _seed(engine):
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        admin = Climber(username="admin", password="x", user_scope=UserScope.admin)
        approved = Climber(username="approved", password="x")
        pending = Climber(username="pending", password="x")
        season = Season(name="S", year=2026)
        s.add_all([admin, approved, pending, season])
        await s.flush()
        comp = Competition(
            name="C", comp_type=CompType.QUALIFIER,
            comp_date=date(2026, 6, 15), season_id=season.id,
        )
        s.add(comp)
        await s.flush()
        problem = Problem(competition_id=comp.id, level_no=LEVEL, problem_no=1)
        s.add(problem)
        await s.flush()
        for climber, is_approved in ((approved, True), (pending, False)):
            s.add(Registration(
                comp_id=comp.id, user_id=climber.id, level=LEVEL, approved=is_approved,
            ))
            s.add(ProblemScore(
                competition_id=comp.id, problem_id=problem.id, user_id=climber.id,
                attempts_total=1, got_bonus=True, got_top=True,
                attempts_to_bonus=1, attempts_to_top=1, ifsc_score=25.0,
            ))
        await s.commit()
        headers = {"Authorization": f"Bearer {create_access_token(admin.id)}"}
        return comp.id, headers


async def test_level_stats_ignore_unapproved_registrations(client, engine):
    comp_id, headers = await _seed(engine)

    resp = await client.get(
        f"/api/v1/competitions/{comp_id}/level/{LEVEL}/stats", headers=headers,
    )

    assert resp.status_code == 200, resp.text
    [stats] = resp.json()["problems"]
    assert stats["total_competitors"] == 1
    assert stats["got_top_count"] == 1
    assert stats["got_bonus_count"] == 1
    assert stats["got_top_percentage"] == 100.0


async def test_level_stats_list_climbers_sorted(client, engine):
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        admin = Climber(username="admin", password="x", user_scope=UserScope.admin)
        zed = Climber(username="zed", password="x", firstname="Zed", lastname="Alpha")
        anna = Climber(username="anna", password="x", firstname="anna", lastname="Berg")
        bonus_only = Climber(username="bo", password="x", firstname="Bo", lastname="Nus")
        anon = Climber(username="Anon", password="x")
        season = Season(name="S", year=2026)
        s.add_all([admin, zed, anna, bonus_only, anon, season])
        await s.flush()
        comp = Competition(
            name="C", comp_type=CompType.QUALIFIER,
            comp_date=date(2026, 6, 15), season_id=season.id,
        )
        s.add(comp)
        await s.flush()
        problem = Problem(competition_id=comp.id, level_no=LEVEL, problem_no=1)
        s.add(problem)
        await s.flush()
        for climber, top in ((zed, True), (anna, True), (anon, True), (bonus_only, False)):
            s.add(Registration(
                comp_id=comp.id, user_id=climber.id, level=LEVEL, approved=True,
            ))
            s.add(ProblemScore(
                competition_id=comp.id, problem_id=problem.id, user_id=climber.id,
                attempts_total=1, got_bonus=True, got_top=top,
                attempts_to_bonus=1, attempts_to_top=1 if top else None,
                ifsc_score=25.0 if top else 10.0,
            ))
        await s.commit()
        comp_id = comp.id
        headers = {"Authorization": f"Bearer {create_access_token(admin.id)}"}

    resp = await client.get(
        f"/api/v1/competitions/{comp_id}/level/{LEVEL}/stats", headers=headers,
    )

    assert resp.status_code == 200, resp.text
    [stats] = resp.json()["problems"]
    top_names = [c["username"] for c in stats["got_top_climbers"]]
    bonus_names = [c["username"] for c in stats["got_bonus_climbers"]]
    # Sorted case-insensitively on the shown name (full name, else username)
    assert top_names == ["anna", "Anon", "zed"]
    assert bonus_names == ["anna", "Anon", "bo", "zed"]
    assert "bo" not in top_names
    assert stats["got_top_count"] == len(top_names)
    assert stats["got_bonus_count"] == len(bonus_names)
    assert stats["got_top_climbers"][0] == {
        "id": stats["got_top_climbers"][0]["id"],
        "username": "anna", "firstname": "anna", "lastname": "Berg",
    }


async def test_level_stats_forbidden_for_non_admin(client, engine):
    comp_id, _ = await _seed(engine)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        climber = Climber(username="climber", password="x")
        s.add(climber)
        await s.commit()
        headers = {"Authorization": f"Bearer {create_access_token(climber.id)}"}

    resp = await client.get(
        f"/api/v1/competitions/{comp_id}/level/{LEVEL}/stats", headers=headers,
    )

    assert resp.status_code == 403
    assert "got_top_climbers" not in resp.text
