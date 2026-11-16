from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Climber, Competition, Registration, UserScope
from schema.climber import (
    AdminClimberUpdate,
    ClimberCreate,
    ClimberOut,
    ClimberUpdate,
    resolve_username_change,
)
from schema.registration import RegistrationWithCompetitionOut
from security.deps import CurrentUser, AdminUser
from security.hashing import hash_password_async

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



async def apply_username_change(
    session: AsyncSession, updates: dict, current_username: str, climber_id: int
) -> None:
    """
    Resolve a requested username in ``updates`` against the stored one.

    Resending the unchanged (possibly legacy) username is dropped from the
    update; a real change must follow the new-username rule (422) and be
    free (409).
    """
    if "username" not in updates:
        return
    try:
        new_username = resolve_username_change(updates["username"], current_username)
    except ValueError as error:
        raise RequestValidationError(
            [{"loc": ("body", "username"), "msg": f"Value error, {error}", "type": "value_error"}]
        ) from None
    if new_username is None:
        del updates["username"]
        return
    await check_username_available(session, new_username, exclude_id=climber_id)
    updates["username"] = new_username


@router.post("", response_model=ClimberOut, status_code=status.HTTP_201_CREATED)
async def create_climber(payload: ClimberCreate, session: Session):
    await check_username_available(session, payload.username)
    await check_email_available(session, payload.email)

    climber_data = payload.model_dump(exclude={'password'})
    climber = Climber(**climber_data, password=await hash_password_async(payload.password))
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
    await apply_username_change(session, updates, current.username, current.id)

    # Hash password if provided
    if 'password' in updates:
        updates['password'] = await hash_password_async(updates['password'])

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
async def update_climber(climber_id: int, payload: AdminClimberUpdate, admin: AdminUser, session: Session):
    """
    Update a climber by ID. Admin only.
    """
    climber = await session.scalar(select(Climber).where(Climber.id == climber_id))
    if not climber:
        raise HTTPException(status_code=404, detail="Climber not found")

    updates = payload.model_dump(exclude_unset=True)
    await apply_username_change(session, updates, climber.username, climber.id)

    # Hash password if provided
    if 'password' in updates:
        updates['password'] = await hash_password_async(updates['password'])

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
async def get_climber(climber_id: int, current: CurrentUser, session: Session):
    """
    Get your own profile or, as an admin, another climber's profile.
    """
    if climber_id != current.id and "admin" not in current.effective_scopes:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    result = await session.execute(select(Climber).where(Climber.id == climber_id))
    climber = result.scalar_one_or_none()
    if climber is None:
        raise HTTPException(status_code=404, detail="Climber not found")

    return climber


@router.get("/{climber_id}/registrations", response_model=List[RegistrationWithCompetitionOut])
async def get_climber_registrations(climber_id: int, admin: AdminUser, session: Session):
    """
    Get all competition registrations for a specific climber. Admin only.
    """
    climber = await session.get(Climber, climber_id)
    if climber is None:
        raise HTTPException(status_code=404, detail="Climber not found")

    rows = (await session.execute(
        select(Registration, Competition.name, Competition.comp_date)
        .join(Competition, Registration.comp_id == Competition.id)
        .where(Registration.user_id == climber_id)
        .order_by(Competition.comp_date.desc())
    )).all()

    return [
        RegistrationWithCompetitionOut(
            comp_id=reg.comp_id,
            competition_name=competition_name,
            comp_date=comp_date,
            level=reg.level,
            approved=reg.approved,
            created_at=reg.created_at,
        )
        for reg, competition_name, comp_date in rows
    ]
