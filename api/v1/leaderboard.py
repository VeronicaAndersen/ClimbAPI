from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func, Integer
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Competition, ProblemScore, Climber, Registration
from schema.leaderboard import LeaderboardEntry, LevelLeaderboard
from security.deps import CurrentUser

router = APIRouter(tags=["leaderboard"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get(
    "/competition/{comp_id}/leaderboard/level/{level}",
    response_model=LevelLeaderboard,
    status_code=status.HTTP_200_OK,
)
async def get_leaderboard(
    comp_id: int,
    level: int,
    current: CurrentUser,
    session: SessionDep,
):
    """
    Get the top 10 leaderboard for a specific competition and level.
    Based on IFSC scoring rules:
    - Total IFSC score (descending)
    - Number of tops (descending)
    - Total attempts to top (ascending)
    - Number of bonuses (descending)
    - Total attempts to bonus (ascending)
    """
    # Verify competition exists
    comp_exists = await session.scalar(
        select(func.count()).select_from(Competition).where(Competition.id == comp_id)
    )
    if not comp_exists:
        raise HTTPException(status_code=404, detail="Competition not found")

    # Get aggregated scores for each climber in this competition and level
    # Only include approved registrations
    query = (
        select(
            Climber.id.label("climber_id"),
            Climber.name.label("climber_name"),
            func.sum(ProblemScore.ifsc_score).label("total_score"),
            func.sum(func.cast(ProblemScore.got_top, Integer)).label("tops"),
            func.sum(func.cast(ProblemScore.got_bonus, Integer)).label("bonuses"),
            func.sum(
                func.coalesce(ProblemScore.attempts_to_top, 0)
            ).label("attempts_to_top"),
            func.sum(
                func.coalesce(ProblemScore.attempts_to_bonus, 0)
            ).label("attempts_to_bonus"),
        )
        .select_from(ProblemScore)
        .join(Climber, ProblemScore.user_id == Climber.id)
        .join(
            Registration,
            (Registration.comp_id == comp_id)
            & (Registration.user_id == Climber.id)
            & (Registration.level == level)
            & (Registration.approved == True),  # Only approved registrations
        )
        .where(ProblemScore.competition_id == comp_id)
        .group_by(Climber.id, Climber.name)
        .order_by(
            func.sum(ProblemScore.ifsc_score).desc(),
            func.sum(func.cast(ProblemScore.got_top, Integer)).desc(),
            func.sum(func.coalesce(ProblemScore.attempts_to_top, 0)).asc(),
            func.sum(func.cast(ProblemScore.got_bonus, Integer)).desc(),
            func.sum(func.coalesce(ProblemScore.attempts_to_bonus, 0)).asc(),
        )
        .limit(10)
    )

    result = await session.execute(query)
    rows = result.all()

    entries = []
    for rank, row in enumerate(rows, start=1):
        entries.append(
            LeaderboardEntry(
                rank=rank,
                climber_id=row.climber_id,
                climber_name=row.climber_name,
                total_score=float(row.total_score or 0),
                tops=int(row.tops or 0),
                bonuses=int(row.bonuses or 0),
                attempts_to_top=int(row.attempts_to_top or 0),
                attempts_to_bonus=int(row.attempts_to_bonus or 0),
            )
        )

    return LevelLeaderboard(
        competition_id=comp_id,
        level=level,
        entries=entries,
    )
