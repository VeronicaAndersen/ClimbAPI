from collections import defaultdict
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from db.config import get_session
from db.models import Climber, Competition, Problem, ProblemScore, Registration
from schema.competition import (
    CompetitionCreate,
    CompetitionOut,
    CompetitionUpdate,
    LeaderboardEntry,
    LeaderboardResponse,
    LevelLeaderboard,
)
from security.deps import AdminUser

router = APIRouter(prefix="/competition", tags=["competition"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def seed_problems(session: AsyncSession, comp_id: int, levels: int = 10, per_level: int = 8) -> int:
    rows = [
        {"competition_id": comp_id, "level_no": lvl, "problem_no": prob}
        for lvl in range(1, levels + 1)
        for prob in range(1, per_level + 1)
    ]
    stmt = insert(Problem).values(rows).on_conflict_do_nothing(
        index_elements=[Problem.competition_id, Problem.level_no, Problem.problem_no]
    ).returning(Problem.id)
    res = await session.execute(stmt)

    inserted = len(res.fetchall())
    return inserted


@router.post("", response_model=CompetitionOut, status_code=status.HTTP_201_CREATED)
async def create_competition(
        payload: CompetitionCreate,
        session: SessionDep,
        _: AdminUser,
):
    comp = Competition(**payload.model_dump())
    session.add(comp)
    await session.flush()
    await seed_problems(session, comp.id, levels=7, per_level=8)
    await session.commit()
    await session.refresh(comp)
    return comp


@router.get("/{comp_id}/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard(comp_id: int, session: SessionDep, _: AdminUser):
    if not await session.get(Competition, comp_id):
        raise HTTPException(status_code=404, detail="Competition not found")

    subq = (
        select(
            Registration.level,
            Climber.firstname,
            Climber.lastname,
            Climber.username,
            func.sum(ProblemScore.ifsc_score).label("total_score"),
            func.rank().over(
                partition_by=Registration.level,
                order_by=func.sum(ProblemScore.ifsc_score).desc(),
            ).label("rank"),
        )
        .join(Climber, Climber.id == Registration.user_id)
        .join(
            ProblemScore,
            and_(
                ProblemScore.user_id == Registration.user_id,
                ProblemScore.competition_id == Registration.comp_id,
            ),
        )
        .where(
            and_(
                Registration.comp_id == comp_id,
                Registration.approved == True,
            )
        )
        .group_by(
            Registration.level,
            Climber.id,
            Climber.firstname,
            Climber.lastname,
            Climber.username,
        )
        .subquery()
    )

    rows = (
        await session.execute(
            select(subq).where(subq.c.rank <= 10).order_by(subq.c.level, subq.c.rank)
        )
    ).all()

    by_level: dict[int, list[LeaderboardEntry]] = defaultdict(list)
    for row in rows:
        firstname = row.firstname or ""
        lastname = row.lastname or ""
        full_name = (firstname + " " + lastname).strip() or row.username
        by_level[row.level].append(
            LeaderboardEntry(rank=row.rank, name=full_name, total_score=row.total_score)
        )

    return LeaderboardResponse(
        competition_id=comp_id,
        levels=[
            LevelLeaderboard(level=lvl, entries=entries)
            for lvl, entries in sorted(by_level.items())
        ],
    )


@router.get("/{comp_id}", response_model=CompetitionOut)
async def get_competition(comp_id: int, session: SessionDep):
    comp = await session.get(Competition, comp_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    return comp


@router.get("", response_model=List[CompetitionOut])
async def list_competitions(
        session: SessionDep,
        comp: Optional[CompetitionUpdate] = None,
):
    stmt = select(Competition)
    if comp is not None:
        data = comp.model_dump(exclude_unset=True, exclude_none=True)
        filters = []
        for k, v in data.items():
            col: Optional[InstrumentedAttribute] = getattr(Competition, k, None)
            if col is None:
                continue
            if isinstance(v, (list, tuple, set)):
                if v:  # only add if non-empty
                    filters.append(col.in_(v))
            else:
                filters.append(col == v)
        if filters:
            stmt = stmt.where(*filters)

    rows = (await session.execute(stmt.order_by(Competition.comp_date.asc()))).scalars().all()
    return rows


@router.patch("/{comp_id}", response_model=CompetitionOut)
async def update_competition(
        comp_id: int,
        body: CompetitionUpdate,
        session: SessionDep,
        _: AdminUser,
):
    comp = await session.get(Competition, comp_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")

    incoming = body.model_dump(exclude_unset=True)
    merged = {
        "name": incoming.get("name", comp.name),
        "description": incoming.get("description", comp.description),
        "comp_type": incoming.get("comp_type", comp.comp_type),
        "comp_date": incoming.get("comp_date", comp.comp_date),
        "season_id": incoming.get("season_id", comp.season_id),
        "round_no": incoming.get("round_no", comp.round_no),
    }
    _ = CompetitionCreate.model_validate(merged)

    for k, v in incoming.items():
        setattr(comp, k, v)

    await session.flush()
    await session.commit()
    await session.refresh(comp)
    return comp


@router.delete("/{comp_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_competition(
        comp_id: int,
        session: SessionDep,
        _: AdminUser,
):
    comp = await session.get(Competition, comp_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    await session.delete(comp)
    await session.commit()
    return None
