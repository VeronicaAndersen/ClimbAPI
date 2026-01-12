from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Competition, Registration, Climber
from schema.registration import RegistrationCreate, RegistrationOut, RegistrationWithClimberOut, RegistrationApprovalUpdate
from security.deps import CurrentUser, AdminUser
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
    await session.commit()
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


# Admin: Get all registrations for a competition with climber names
@router.get("/competition/{comp_id}/registrations",
            response_model=List[RegistrationWithClimberOut],
            status_code=status.HTTP_200_OK)
async def get_all_registrations(
        comp_id: int,
        session: SessionDep,
        admin: AdminUser,
):
    """
    Get all registrations for a specific competition. Admin only.
    Returns registrations with climber names.
    """
    # Verify competition exists
    comp_exists = await session.scalar(
        select(func.count()).select_from(Competition).where(Competition.id == comp_id)
    )
    if not comp_exists:
        raise HTTPException(status_code=404, detail="Competition not found")

    # Get registrations with climber info
    result = await session.execute(
        select(Registration, Climber.name)
        .join(Climber, Registration.user_id == Climber.id)
        .where(Registration.comp_id == comp_id)
        .order_by(Registration.created_at.desc())
    )

    registrations = []
    for reg, climber_name in result.all():
        registrations.append(
            RegistrationWithClimberOut(
                comp_id=reg.comp_id,
                user_id=reg.user_id,
                level=reg.level,
                approved=reg.approved,
                created_at=reg.created_at,
                climber_name=climber_name,
            )
        )

    return registrations


# Admin: Approve or reject a registration
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
    """
    Approve or reject a registration. Admin only.
    """
    # Get the registration
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