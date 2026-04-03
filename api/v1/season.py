from itertools import groupby
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Climber, Competition, Problem, ProblemScore, Registration, Season
from schema.season import (
    SeasonCreate,
    SeasonOut,
    SeasonStandingsResponse,
    SeasonUpdate,
    LevelStandings,
    SeasonStandingsEntry,
)
from security.deps import AdminUser

router = APIRouter(prefix="/season", tags=["season"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=SeasonOut, status_code=status.HTTP_201_CREATED)
async def create_season(payload: SeasonCreate, session: SessionDep, _: AdminUser):
    existing = await session.scalar(
        select(Season).where(
            Season.name == payload.name
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="Season with this name already exists")
    season = Season(**payload.model_dump())
    session.add(season)
    await session.flush()
    await session.commit()
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


@router.get("/{season_id}/standings", response_model=SeasonStandingsResponse)
async def get_season_standings(season_id: int, session: SessionDep, _: AdminUser):
    season = await session.get(Season, season_id)
    if not season:
        raise HTTPException(status_code=404, detail="Season not found")

    # Aggregate scores per (user, level) across all approved registrations in this season.
    # Joining through Problem enforces level isolation: a climber who changed levels
    # in a competition only contributes scores from problems at their registered level.
    scores_sub = (
        select(
            Registration.user_id,
            Registration.level,
            func.sum(ProblemScore.ifsc_score).label("total_score"),
        )
        .join(Competition, and_(Competition.id == Registration.comp_id, Competition.season_id == season_id))
        .join(Problem, and_(Problem.competition_id == Registration.comp_id, Problem.level_no == Registration.level))
        .join(ProblemScore, and_(ProblemScore.problem_id == Problem.id, ProblemScore.user_id == Registration.user_id))
        .where(Registration.approved.is_(True))
        .group_by(Registration.user_id, Registration.level)
        .subquery()
    )

    ranked_sub = (
        select(
            scores_sub.c.level,
            scores_sub.c.total_score,
            Climber.firstname,
            Climber.lastname,
            Climber.username,
            func.rank()
            .over(
                partition_by=scores_sub.c.level,
                order_by=scores_sub.c.total_score.desc(),
            )
            .label("rank"),
        )
        .join(Climber, Climber.id == scores_sub.c.user_id)
        .subquery()
    )

    rows = (
        await session.execute(
            select(ranked_sub).order_by(ranked_sub.c.level.asc(), ranked_sub.c.rank.asc())
        )
    ).mappings().all()

    levels: list[LevelStandings] = []
    for level, group in groupby(rows, key=lambda r: r["level"]):
        entries = [
            SeasonStandingsEntry(
                rank=r["rank"],
                name=(
                    f"{r['firstname']} {r['lastname']}".strip()
                    if r["firstname"] or r["lastname"]
                    else r["username"]
                ),
                total_score=r["total_score"],
            )
            for r in group
        ]
        levels.append(LevelStandings(level=level, entries=entries))

    return SeasonStandingsResponse(season_id=season_id, season_name=season.name, levels=levels)


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
    await session.commit()
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
    await session.commit()
    return None
