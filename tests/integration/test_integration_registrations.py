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


async def _seed_my_registrations(engine):
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        me = Climber(username="me", password="x", user_scope=UserScope.climber)
        other = Climber(username="other", password="x", user_scope=UserScope.climber)
        lonely = Climber(username="lonely", password="x", user_scope=UserScope.climber)
        season = Season(name="S", year=2026)
        s.add_all([me, other, lonely, season])
        await s.flush()
        old = Competition(name="Old", comp_type=CompType.QUALIFIER,
                          comp_date=date(2026, 3, 1), season_id=season.id)
        new = Competition(name="New", comp_type=CompType.QUALIFIER,
                          comp_date=date(2026, 9, 1), season_id=season.id)
        s.add_all([old, new])
        await s.flush()
        s.add_all([
            Registration(comp_id=old.id, user_id=me.id, level=1, approved=True),
            Registration(comp_id=new.id, user_id=me.id, level=2),
            Registration(comp_id=new.id, user_id=other.id, level=3),
        ])
        await s.commit()
        auth = lambda u: {"Authorization": f"Bearer {create_access_token(u.id)}"}
        return auth(me), auth(lonely), old.id, new.id


async def test_my_registrations_only_own_ordered_by_comp_date_desc(client, engine):
    me, _, old_id, new_id = await _seed_my_registrations(engine)

    res = await client.get("/api/v1/climber/me/registrations", headers=me)

    assert res.status_code == 200, res.text
    body = res.json()
    assert [r["comp_id"] for r in body] == [new_id, old_id]
    assert [r["competition_name"] for r in body] == ["New", "Old"]
    assert [(r["level"], r["approved"]) for r in body] == [(2, False), (1, True)]


async def test_my_registrations_empty_list_when_none(client, engine):
    _, lonely, _, _ = await _seed_my_registrations(engine)

    res = await client.get("/api/v1/climber/me/registrations", headers=lonely)

    assert res.status_code == 200
    assert res.json() == []


async def test_my_registrations_requires_auth(client, engine):
    await _seed_my_registrations(engine)

    assert (await client.get("/api/v1/climber/me/registrations")).status_code == 401
