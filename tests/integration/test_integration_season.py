"""Integration tests for the season endpoints."""
from sqlalchemy.ext.asyncio import async_sessionmaker

from db.models import Climber, UserScope
from security.jwt_tools import create_access_token

BASE = "/api/v1"


async def test_deleting_missing_season_returns_season_not_found(client, engine):
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        admin = Climber(username="admin", password="x", user_scope=UserScope.admin)
        s.add(admin)
        await s.commit()
    headers = {"Authorization": f"Bearer {create_access_token(admin.id)}"}

    response = await client.delete(f"{BASE}/season/999", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Season not found"
