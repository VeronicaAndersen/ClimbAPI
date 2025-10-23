from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Competition, Problem
from schema.competition import CompetitionCreate, CompetitionOut, CompetitionUpdate
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
    await session.refresh(comp)
    return comp


@router.get("/{comp_id}", response_model=CompetitionOut)
async def get_competition(comp_id: int, session: SessionDep):
    comp = await session.get(Competition, comp_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    return comp


@router.get("", response_model=List[CompetitionOut])
async def list_competitions(
        session: SessionDep,
        season_id: Optional[int] = Query(None),
        comp_type: Optional[str] = Query(None),
):
    stmt = select(Competition)
    if season_id is not None:
        stmt = stmt.where(Competition.season_id == season_id)
    if comp_type is not None:
        stmt = stmt.where(Competition.comp_type == comp_type)
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
    return None
