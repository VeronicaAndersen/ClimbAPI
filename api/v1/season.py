from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Season
from schema.season import SeasonCreate, SeasonOut, SeasonUpdate
from security.deps import AdminUser

router = APIRouter(prefix="/season", tags=["season"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=SeasonOut, status_code=status.HTTP_201_CREATED)
async def create_season(payload: SeasonCreate, session: SessionDep, _: AdminUser):
    season = Season(**payload.model_dump())
    session.add(season)
    await session.flush()
    await session.refresh(season)
    return season


@router.get("/{season_id}", response_model=SeasonOut, status_code=status.HTTP_200_OK)
async def get_season(season_id: int, session: SessionDep, _: AdminUser):
    season = await session.get(Season, season_id)
    if not season:
        raise HTTPException(status_code=404, detail="Season not found")
    return season


@router.get("", response_model=List[SeasonOut])
async def list_seasons(
        session: SessionDep,
        _: AdminUser,
        s: Optional[SeasonUpdate] = None,
):
    stmt = select(Season)
    if s is not None:
        if s.year is not None:
            stmt = stmt.filter(Season.year == s.year)
        if s.name is not None:
            stmt = stmt.filter(Season.name == s.name)
    rows = (await session.execute(stmt.order_by(Season.created_at.asc()))).scalars().all()
    return rows


@router.patch("/{season_id}", response_model=SeasonOut, status_code=status.HTTP_200_OK)
async def update_season(
        season_id: int,
        body: SeasonUpdate,
        session: SessionDep,
        _: AdminUser,
):
    season = await session.get(Season, season_id)
    if not season:
        raise HTTPException(status_code=404, detail="Season not found")

    incoming = body.model_dump(exclude_unset=True)
    merged = {
        "name": incoming.get("name", season.name),
        "year": incoming.get("year", season.year),
        "created_at": incoming.get("created_at", season.created_at),
    }
    _ = SeasonCreate.model_validate(merged)

    for k, v in incoming.items():
        setattr(season, k, v)

    await session.flush()
    await session.refresh(season)
    return season


@router.delete("/{season_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_season(
        season_id: int,
        session: SessionDep,
        _: AdminUser,
):
    season = await session.get(Season, season_id)
    if not season:
        raise HTTPException(status_code=404, detail="Competition not found")
    await session.delete(season)
    return None
