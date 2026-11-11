import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Climber, PasswordResetToken
from schema.auth import (
    TokenPair,
    LoginRequest,
    RefreshRequest,
    PasswordResetRequest,
    PasswordResetConfirm,
    MessageResponse,
)
from schema.climber import ClimberCreate, AuthOut
from security.hashing import (
    hash_password_async,
    needs_rehash,
    verify_dummy_password_async,
    verify_password_async,
)
from security.jwt_tools import create_access_token, create_refresh_token, decode_token
from security.login_throttle import login_throttle
from security.rate_limit import (
    RATE_LIMITED_RESPONSE,
    TOO_MANY_REQUESTS_DETAIL,
    login_rate_limit,
    password_reset_confirm_rate_limit,
    password_reset_request_rate_limit,
    refresh_rate_limit,
    signup_rate_limit,
)
from services.email import send_password_reset_email

Session = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter(prefix="/auth", tags=["auth"])


async def authenticate_user(session: AsyncSession, username: str, password: str) -> Climber:
    """
    Authenticate user and return Climber object.

    Raises 429 when the username has too many recent failed attempts and 401
    when the credentials are invalid.
    """
    retry_after = login_throttle.retry_after(username)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=TOO_MANY_REQUESTS_DETAIL,
            headers={"Retry-After": str(retry_after)},
        )
    # Counted up front so concurrent guesses cannot all pass the check above.
    login_throttle.register_attempt(username)

    user = await session.scalar(select(Climber).where(Climber.username == username))
    if user is None:
        # Do the same hashing work as for a real user so response timing does
        # not reveal whether the username exists.
        await verify_dummy_password_async(password)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not await verify_password_async(password, user.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    login_throttle.reset(username)

    # Opportunistic rehash if password parameters changed
    if needs_rehash(user.password):
        user.password = await hash_password_async(password)
        await session.commit()

    return user


@router.post("/signup", response_model=AuthOut, status_code=status.HTTP_201_CREATED, responses=RATE_LIMITED_RESPONSE)
@signup_rate_limit
async def signup(request: Request, payload: ClimberCreate, session: Session):
    # Check username uniqueness
    exists = await session.scalar(select(Climber.id).where(Climber.username == payload.username))
    if exists:
        raise HTTPException(status_code=409, detail="Username is already taken")

    climber_data = payload.model_dump(exclude={'password'})
    climber = Climber(
        **climber_data,
        user_scope="climber",
        password=await hash_password_async(payload.password),
    )
    session.add(climber)

    try:
        await session.flush()
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Username is already taken")

    await session.refresh(climber)
    return AuthOut(
        climber=climber,
        access_token=create_access_token(climber.id),
        refresh_token=create_refresh_token(climber.id)
    )


@router.post("/login", response_model=TokenPair, responses=RATE_LIMITED_RESPONSE)
@login_rate_limit
async def login(request: Request, body: LoginRequest, session: Session):
    user = await authenticate_user(session, body.username, body.password)
    return TokenPair(
        access_token=create_access_token(user.id, extra={"username": user.username}),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/token", response_model=TokenPair, responses=RATE_LIMITED_RESPONSE)
@login_rate_limit
async def token(request: Request, form_data: Annotated[OAuth2PasswordRequestForm, Depends()], session: Session):
    """
    OAuth2 compatible token endpoint for Swagger UI authorization.
    Uses the same logic as /login but accepts OAuth2PasswordRequestForm.
    """
    user = await authenticate_user(session, form_data.username, form_data.password)
    return TokenPair(
        access_token=create_access_token(user.id, extra={"username": user.username}),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/password-reset/request", response_model=MessageResponse, responses=RATE_LIMITED_RESPONSE)
@password_reset_request_rate_limit
async def request_password_reset(
    request: Request,
    body: PasswordResetRequest,
    background_tasks: BackgroundTasks,
    session: Session,
):
    username = body.username.strip().lower()
    user = await session.scalar(select(Climber).where(Climber.username == username))

    if user and user.email:
        # Invalidate any existing unused tokens for this user
        existing = await session.scalars(
            select(PasswordResetToken).where(
                and_(PasswordResetToken.user_id == user.id, PasswordResetToken.used == False)
            )
        )
        for t in existing:
            await session.delete(t)

        token = secrets.token_urlsafe(32)
        reset_token = PasswordResetToken(
            user_id=user.id,
            token=token,
            expires_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
        )
        session.add(reset_token)
        await session.commit()

        background_tasks.add_task(
            send_password_reset_email,
            email=user.email,
            token=token,
            firstname=user.firstname,
        )

    # Always return the same message to avoid revealing whether the email exists
    return MessageResponse(message="Om e-postadressen finns i systemet har ett mail skickats.")


@router.post("/password-reset/confirm", response_model=MessageResponse, responses=RATE_LIMITED_RESPONSE)
@password_reset_confirm_rate_limit
async def confirm_password_reset(request: Request, body: PasswordResetConfirm, session: Session):
    now = datetime.now(tz=timezone.utc)
    reset_token = await session.scalar(
        select(PasswordResetToken).where(
            and_(
                PasswordResetToken.token == body.token,
                PasswordResetToken.used == False,
                PasswordResetToken.expires_at > now,
            )
        )
    )

    if not reset_token:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user = await session.get(Climber, reset_token.user_id)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user.password = await hash_password_async(body.new_password)
    reset_token.used = True
    await session.commit()

    return MessageResponse(message="Lösenordet har ändrats.")


@router.post("/refresh", response_model=TokenPair, responses=RATE_LIMITED_RESPONSE)
@refresh_rate_limit
async def refresh(request: Request, body: RefreshRequest, session: Session):
    try:
        payload = decode_token(body.refresh_token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Wrong token type")
    uid = int(payload["sub"])
    user = await session.get(Climber, uid)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return TokenPair(
        access_token=create_access_token(uid),
        refresh_token=create_refresh_token(uid),  # rotation; add blacklist if needed
    )
