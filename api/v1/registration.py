from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Climber, Competition, Problem, ProblemScore, Registration
from schema.registration import (
    RegistrationCreate,
    RegistrationOut,
    RegistrationWithClimberOut,
    RegistrationApprovalUpdate,
    RegistrationLevelUpdate,
)
from security.deps import AdminUser, CurrentUser

router = APIRouter(tags=["registration"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _create_empty_scores(
    session: AsyncSession, comp_id: int, user_id: int, level: int, skip_existing: bool = False
) -> None:
    problems = (await session.execute(
        select(Problem).where(
            Problem.competition_id == comp_id,
            Problem.level_no == level,
        )
    )).scalars().all()

    if skip_existing:
        existing_ids = set((await session.execute(
            select(ProblemScore.problem_id).where(
                ProblemScore.competition_id == comp_id,
                ProblemScore.user_id == user_id,
            )
        )).scalars().all())
    else:
        existing_ids = set()

    for p in problems:
        if p.id not in existing_ids:
            session.add(ProblemScore(
                competition_id=comp_id,
                problem_id=p.id,
                user_id=user_id,
                attempts_total=0,
                got_bonus=False,
                got_top=False,
                attempts_to_bonus=0,
                attempts_to_top=0,
            ))


@router.post("/competition/{comp_id}/register",
             response_model=RegistrationOut,
             status_code=status.HTTP_201_CREATED)
async def register_self(
        comp_id: int,
        body: RegistrationCreate,
        session: SessionDep,
        current: CurrentUser,
):
    if not await session.get(Competition, comp_id):
        raise HTTPException(status_code=404, detail="Competition not found")

    already = await session.scalar(
        select(func.count()).select_from(Registration).where(
            Registration.comp_id == comp_id,
            Registration.user_id == current.id,
        )
    )
    if already:
        raise HTTPException(status_code=409, detail="Already registered")

    reg = Registration(comp_id=comp_id, user_id=current.id, level=body.level)
    session.add(reg)
    await session.flush()
    await session.refresh(reg)

    await _create_empty_scores(session, comp_id, current.id, body.level)

    await session.flush()
    await session.commit()
    return reg


@router.get("/competition/{comp_id}/registration",
            response_model=RegistrationOut | None,
            status_code=status.HTTP_200_OK)
async def get_my_registration(
        comp_id: int,
        session: SessionDep,
        current: CurrentUser,
):
    return await session.scalar(
        select(Registration).where(
            Registration.comp_id == comp_id,
            Registration.user_id == current.id,
        )
    )


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


@router.get("/competition/{comp_id}/registrations",
            response_model=List[RegistrationWithClimberOut],
            status_code=status.HTTP_200_OK)
async def get_all_registrations(
        comp_id: int,
        session: SessionDep,
        admin: AdminUser,
):
    if not await session.get(Competition, comp_id):
        raise HTTPException(status_code=404, detail="Competition not found")

    rows = (await session.execute(
        select(Registration, Climber.username)
        .join(Climber, Registration.user_id == Climber.id)
        .where(Registration.comp_id == comp_id)
        .order_by(Registration.created_at.desc())
    )).all()

    return [
        RegistrationWithClimberOut(
            comp_id=reg.comp_id,
            user_id=reg.user_id,
            level=reg.level,
            approved=reg.approved,
            created_at=reg.created_at,
            climber_name=climber_name,
        )
        for reg, climber_name in rows
    ]


@router.patch("/competition/{comp_id}/registration/{user_id}",
              response_model=RegistrationOut,
              status_code=status.HTTP_200_OK)
async def update_registration_approval(
        comp_id: int,
        user_id: int,
        payload: RegistrationApprovalUpdate,
        session: SessionDep,
        admin: AdminUser,
):
    reg = await session.scalar(
        select(Registration).where(
            Registration.comp_id == comp_id,
            Registration.user_id == user_id,
        )
    )
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")

    reg.approved = payload.approved
    await session.commit()
    await session.refresh(reg)
    return reg


@router.patch("/competition/{comp_id}/registration/{user_id}/level",
              response_model=RegistrationOut,
              status_code=status.HTTP_200_OK)
async def update_registration_level(
        comp_id: int,
        user_id: int,
        payload: RegistrationLevelUpdate,
        session: SessionDep,
        admin: AdminUser,
):
    reg = await session.scalar(
        select(Registration).where(
            Registration.comp_id == comp_id,
            Registration.user_id == user_id,
        )
    )
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")

    if reg.level == payload.level:
        return reg

    reg.level = payload.level
    await _create_empty_scores(session, comp_id, user_id, payload.level, skip_existing=True)

    await session.commit()
    await session.refresh(reg)
    return reg
