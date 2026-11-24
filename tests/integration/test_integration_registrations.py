"""Integration tests for the admin registrations list of a competition."""
from datetime import date

from sqlalchemy.ext.asyncio import async_sessionmaker

from db.models import Climber, Competition, CompType, Registration, Season, UserScope
from security.jwt_tools import create_access_token


async def _seed(engine):
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        admin = Climber(username="admin", password="x", user_scope=UserScope.admin)
        named = Climber(
            username="named", password="x", user_scope=UserScope.climber,
            firstname="Ada", lastname="Lind",
        )
        unnamed = Climber(username="unnamed", password="x", user_scope=UserScope.climber)
        season = Season(name="S", year=2026)
        s.add_all([admin, named, unnamed, season])
        await s.flush()
        comp = Competition(
            name="C", comp_type=CompType.QUALIFIER,
            comp_date=date(2026, 6, 15), season_id=season.id,
        )
        s.add(comp)
        await s.flush()
        s.add_all([
            Registration(comp_id=comp.id, user_id=named.id, level=1, approved=True),
            Registration(comp_id=comp.id, user_id=unnamed.id, level=2),
        ])
        await s.commit()
        headers = {"Authorization": f"Bearer {create_access_token(admin.id)}"}
        climber_headers = {"Authorization": f"Bearer {create_access_token(named.id)}"}
        return comp.id, headers, climber_headers


async def test_registrations_include_climber_first_and_last_name(client, engine):
    comp_id, headers, _ = await _seed(engine)

    res = await client.get(f"/api/v1/competition/{comp_id}/registrations", headers=headers)

    assert res.status_code == 200
    by_username = {r["climber_name"]: r for r in res.json()}
    assert by_username["named"]["firstname"] == "Ada"
    assert by_username["named"]["lastname"] == "Lind"
    assert by_username["unnamed"]["firstname"] is None
    assert by_username["unnamed"]["lastname"] is None


async def test_registrations_with_names_are_admin_only(client, engine):
    comp_id, _, climber_headers = await _seed(engine)
    url = f"/api/v1/competition/{comp_id}/registrations"

    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers=climber_headers)).status_code == 403


async def test_registering_twice_returns_409_and_keeps_first_registration(client, engine):
    # The unique (comp_id, user_id) key decides, so a concurrent duplicate takes this path too.
    comp_id, _, climber_headers = await _seed(engine)

    resp = await client.post(
        f"/api/v1/competition/{comp_id}/register", json={"level": 3}, headers=climber_headers,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "Already registered"
    mine = await client.get(f"/api/v1/competition/{comp_id}/registration", headers=climber_headers)
    assert mine.json()["level"] == 1
