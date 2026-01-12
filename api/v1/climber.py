from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Climber
from schema.climber import ClimberOut, ClimberCreate, ClimberUpdate
from security.deps import CurrentUser, AdminUser
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
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Name is already taken")

    await session.refresh(climber)
    return climber


@router.get("/me", response_model=ClimberOut)
async def get_me(current: CurrentUser):
    return current


@router.patch("/me", response_model=ClimberOut)
async def update_me(payload: ClimberUpdate, current: CurrentUser, session: Session):
    """
    Update the current user's profile.
    Users can update their own name and password.
    """
    # Check if name is being changed and if it's already taken
    if payload.name is not None and payload.name != current.name:
        exists = await session.scalar(
            select(Climber.id).where(Climber.name == payload.name)
        )
        if exists:
            raise HTTPException(status_code=409, detail="Name is already taken")
        current.name = payload.name

    # Hash and update password if provided
    if payload.password is not None:
        current.password = hash_password(payload.password)

    try:
        await session.commit()
        await session.refresh(current)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Name is already taken")

    return current


@router.get("", response_model=List[ClimberOut])
async def get_all_climbers(admin: AdminUser, session: Session):
    """
    Get all climbers. Admin only.
    """
    result = await session.execute(select(Climber).order_by(Climber.created_at.desc()))
    climbers = result.scalars().all()
    return climbers


@router.get("/{climber_id}", response_model=ClimberOut)
async def get_climber(climber_id: int, session: Session):
    """
    Get a specific climber by ID.
    """
    result = await session.execute(select(Climber).where(Climber.id == climber_id))
    climber = result.scalar_one_or_none()
    if climber is None:
        raise HTTPException(status_code=404, detail="Climber not found")

    return climber

