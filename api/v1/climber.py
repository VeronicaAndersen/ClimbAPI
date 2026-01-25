from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Climber, UserScope
from schema.climber import ClimberOut, ClimberCreate, ClimberUpdate
from security.deps import CurrentUser, AdminUser
from security.hashing import hash_password

Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix='/climber', tags=['climber'])


async def check_username_available(session: AsyncSession, username: str, exclude_id: int = None) -> None:
    """Check if username is available, raise 409 if taken."""
    query = select(Climber.id).where(Climber.username == username)
    if exclude_id:
        query = query.where(Climber.id != exclude_id)

    exists = await session.scalar(query)
    if exists:
        raise HTTPException(status_code=409, detail="Username is already taken")


@router.post("", response_model=ClimberOut, status_code=status.HTTP_201_CREATED)
async def create_climber(payload: ClimberCreate, session: Session):
    await check_username_available(session, payload.username)

    climber_data = payload.model_dump(exclude={'password'})
    climber = Climber(**climber_data, password=hash_password(payload.password))
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
    updates = payload.model_dump(exclude_unset=True)

    # Check username uniqueness if being changed
    if 'username' in updates and updates['username'] != current.username:
        await check_username_available(session, updates['username'], exclude_id=current.id)

    # Hash password if provided
    if 'password' in updates:
        updates['password'] = hash_password(updates['password'])

    # Apply updates
    for field, value in updates.items():
        setattr(current, field, value)

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
    climber = await session.scalar(select(Climber).where(Climber.id == climber_id))
    if not climber:
        raise HTTPException(status_code=404, detail="Climber not found")

    updates = payload.model_dump(exclude_unset=True)

    # Check username uniqueness if being changed
    if 'username' in updates and updates['username'] != climber.username:
        await check_username_available(session, updates['username'], exclude_id=climber.id)

    # Hash password if provided
    if 'password' in updates:
        updates['password'] = hash_password(updates['password'])

    # Validate user scope if provided
    if 'user_scope' in updates:
        valid_scopes = {scope.value for scope in UserScope}
        if updates['user_scope'] not in valid_scopes:
            raise HTTPException(status_code=400, detail="Invalid user scope")

    # Apply updates
    for field, value in updates.items():
        setattr(climber, field, value)

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

