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
        select(Climber.id).where(Climber.username == payload.username)
    )
    if exists:
        raise HTTPException(status_code=409, detail="Username is already taken")

    climber = Climber(
        username=payload.username,
        password=hash_password(payload.password),
        email=payload.email,
        firstname=payload.firstname,
        lastname=payload.lastname,
        club=payload.club,
    )
    session.add(climber)

    try:
        await session.flush()
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Username is already taken")

    await session.refresh(climber)
    return climber


@router.get("/me", response_model=ClimberOut)
async def get_me(current: CurrentUser):
    return current


@router.patch("/me", response_model=ClimberOut)
async def update_me(payload: ClimberUpdate, current: CurrentUser, session: Session):
    """
    Update the current user's profile.
    Users can update their own username and password.
    """
    # Check if username is being changed and if it's already taken
    if payload.username is not None and payload.username != current.username:
        exists = await session.scalar(
            select(Climber.id).where(Climber.username == payload.username)
        )
        if exists:
            raise HTTPException(status_code=409, detail="Username is already taken")
        current.username = payload.username

    # Hash and update password if provided
    if payload.password is not None:
        current.password = hash_password(payload.password)

    # Update other fields if provided
    if payload.email is not None:
        current.email = payload.email
    if payload.firstname is not None:
        current.firstname = payload.firstname
    if payload.lastname is not None:
        current.lastname = payload.lastname
    if payload.club is not None:
        current.club = payload.club

    try:
        await session.commit()
        await session.refresh(current)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Username is already taken")

    return current


@router.get("", response_model=List[ClimberOut])
async def get_all_climbers(admin: AdminUser, session: Session):
    """
    Get all climbers. Admin only.
    """
    result = await session.execute(select(Climber).order_by(Climber.created_at.desc()))
    climbers = result.scalars().all()
    return climbers


@router.patch("/{climber_id}", response_model=ClimberOut)
async def update_climber(climber_id: int, payload: ClimberUpdate, admin: AdminUser, session: Session):
    """
    Update a climber by ID. Admin only.
    """
    # Get the climber to update
    result = await session.execute(select(Climber).where(Climber.id == climber_id))
    climber = result.scalar_one_or_none()
    if climber is None:
        raise HTTPException(status_code=404, detail="Climber not found")

    # Check if username is being changed and if it's already taken
    if payload.username is not None and payload.username != climber.username:
        exists = await session.scalar(
            select(Climber.id).where(Climber.username == payload.username)
        )
        if exists:
            raise HTTPException(status_code=409, detail="Username is already taken")
        climber.username = payload.username

    # Hash and update password if provided
    if payload.password is not None:
        climber.password = hash_password(payload.password)

    # Update other fields if provided
    if payload.email is not None:
        climber.email = payload.email
    if payload.firstname is not None:
        climber.firstname = payload.firstname
    if payload.lastname is not None:
        climber.lastname = payload.lastname
    if payload.club is not None:
        climber.club = payload.club

    # Update user scope if provided
    if payload.user_scope is not None:
        valid_scopes = ["climber", "setter", "analyst", "admin"]
        if payload.user_scope not in valid_scopes:
            raise HTTPException(status_code=400, detail="Invalid user scope")
        climber.user_scope = payload.user_scope

    try:
        await session.commit()
        await session.refresh(climber)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Username is already taken")

    return climber


@router.delete("/{climber_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_climber(climber_id: int, admin: AdminUser, session: Session):
    """
    Delete a climber by ID. Admin only.
    """
    result = await session.execute(select(Climber).where(Climber.id == climber_id))
    climber = result.scalar_one_or_none()
    if climber is None:
        raise HTTPException(status_code=404, detail="Climber not found")

    await session.delete(climber)
    await session.commit()
    return None


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

