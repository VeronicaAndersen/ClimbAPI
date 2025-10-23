from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Climber
from schema.climber import ClimberOut, ClimberCreate
from security.deps import CurrentUser
from security.hashing import hash_password

Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix='/climber', tags=['climber'])


@router.post("", response_model=ClimberOut, status_code=status.HTTP_201_CREATED)
async def create_climber(payload: ClimberCreate, session: Session):
    exists = await session.scalar(
        select(Climber.id).where(Climber.name == payload.name)
    )
    if exists:
        raise HTTPException(status_code=409, detail="Name is already taken")

    climber = Climber(
        name=payload.name,
        password=hash_password(payload.password),
    )
    session.add(climber)

    try:
        await session.flush()
    except IntegrityError:
        # in case of race, fallback to 409
        await session.rollback()
        raise HTTPException(status_code=409, detail="Name is already taken")

    await session.refresh(climber)
    return climber


@router.get("/me", response_model=ClimberOut)
async def get_me(current: CurrentUser):
    return current


@router.get("/{climber_id}", response_model=ClimberOut)
async def get_climber(climber_id: int, session: Session):
    result = await session.execute(select(Climber).where(Climber.id == climber_id))
    climber = result.scalar_one_or_none()
    if climber is None:
        raise HTTPException(status_code=404, detail="Climber not found")

    return climber
