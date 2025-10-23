from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Competition, Registration
from schema.registration import RegistrationCreate, RegistrationOut
from security.deps import CurrentUser

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
    return reg
