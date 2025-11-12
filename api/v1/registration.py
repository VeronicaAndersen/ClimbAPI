from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Competition, Registration
from schema.registration import RegistrationCreate, RegistrationOut
from security.deps import CurrentUser
from db.models import Problem, ProblemScore

router = APIRouter(tags=["registration"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/competition/{comp_id}/register",
             response_model=RegistrationOut,
             status_code=status.HTTP_201_CREATED)
async def register_self(
        comp_id: int,
        body: RegistrationCreate,
        session: SessionDep,
        current: CurrentUser,
):
    # ensure competition exists
    exists_comp = await session.scalar(
        select(func.count()).select_from(Competition).where(Competition.id == comp_id)
    )
    if not exists_comp:
        raise HTTPException(status_code=404, detail="Competition not found")

    # prevent double registration
    already = await session.scalar(
        select(func.count()).select_from(Registration).where(
            Registration.comp_id == comp_id,
            Registration.user_id == current.id,
        )
    )
    if already:
        raise HTTPException(status_code=409, detail="Already registered")

    reg = Registration(
        comp_id=comp_id,
        user_id=current.id,
        level=body.level,
    )
    session.add(reg)
    await session.flush()
    await session.refresh(reg)

    # create empty scores for problems in the same level
    problems = (await session.execute(
        select(Problem)
        .where(
            Problem.competition_id == comp_id,
            Problem.level_no == body.level,
        )
    )).scalars().all()

    for p in problems:
        ps = ProblemScore(
            competition_id=comp_id,
            problem_id=p.id,
            user_id=current.id,
            attempts_total=0,
            got_bonus=False,
            got_top=False,
            attempts_to_bonus=0,
            attempts_to_top=0,
        )
        session.add(ps)

    # final flush and return
    await session.flush()
    return reg


# get my registration for a competition
@router.get("/competition/{comp_id}/registration",
            response_model=RegistrationOut,
            status_code=status.HTTP_200_OK)
async def get_my_registration(  
        comp_id: int,
        session: SessionDep,
        current: CurrentUser,
):
    reg = await session.scalar(
        select(Registration).where(
            Registration.comp_id == comp_id,
            Registration.user_id == current.id,
        )
    )
    if not reg:
        raise HTTPException(status_code=404, detail="Not registered for this competition")
    return reg

# check if user is registered for a competition
@router.get("/competition/{comp_id}/registration/check",
            response_model=bool,
            status_code=status.HTTP_200_OK)
async def check_registration(
        comp_id: int,
        session: SessionDep,
        current: CurrentUser,
):
    return await session.scalar(
        select(func.count()).select_from(Registration).where(
            Registration.comp_id == comp_id,
            Registration.user_id == current.id,
        )
    ) > 0