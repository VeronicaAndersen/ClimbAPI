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
    PASSWORD_MAX_LENGTH,
    USERNAME_MAX_LENGTH,
    TokenPair,
    LoginRequest,
    RefreshRequest,
    PasswordResetRequest,
    PasswordResetConfirm,
    MessageResponse,
)
from schema.climber import (
    AuthOut,
    ClimberCreate,
    contains_unstorable_characters,
    is_valid_normalized_username,
    normalize_new_username,
    normalize_username,
)
from security.client_address import client_ip
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
    RateLimitedRoute,
    TOO_MANY_REQUESTS_DETAIL,
    login_rate_limit,
    password_reset_confirm_rate_limit,
    password_reset_request_rate_limit,
    refresh_rate_limit,
    signup_rate_limit,
)
from services.email import send_password_reset_email

Session = Annotated[AsyncSession, Depends(get_session)]

PASSWORD_RESET_REQUESTED_MESSAGE = "Om e-postadressen finns i systemet har ett mail skickats."

# RateLimitedRoute enforces the @..._rate_limit decorators below.
router = APIRouter(prefix="/auth", tags=["auth"], route_class=RateLimitedRoute)


async def find_climber_by_login_name(session: AsyncSession, raw_username: str) -> Climber | None:
    """
    The account a typed username refers to, for login and password reset.

    Tries two forms in ONE query, so the number of queries does not reveal
    whether an account exists:

    * lenient (strip + lower): how every existing account is found, including
      legacy names that are not NFKC-normalized;
    * strict (strip + NFKC + lower): the stored form of names created under
      the new-username rule, so "ａdmin" or a decomposed "josé" typed exactly
      as at signup still finds the account.

    An exact lenient match wins, so a legacy decomposed "josé" and a newer
    precomposed "josé" each stay reachable when typed exactly.
    """
    lenient = normalize_username(raw_username)
    strict = normalize_new_username(raw_username)
    return await session.scalar(
        select(Climber)
        .where(Climber.username.in_((lenient, strict)))
        .order_by((Climber.username == lenient).desc())
        .limit(1)
    )


async def _check_credentials(
    session: AsyncSession, raw_username: str, password: str
) -> Climber | None:
    """The user if the password matches, else None."""
    user = await find_climber_by_login_name(session, raw_username)
    if user is None:
        # Do the same hashing work as for a real user so response timing does
        # not reveal whether the username exists.
        await verify_dummy_password_async(password)
        return None
    if not await verify_password_async(password, user.password):
        return None
    return user


async def authenticate_user(
    session: AsyncSession, username: str, password: str, client_address: str
) -> Climber:
    """
    Authenticate user and return Climber object.

    Raises 429 when this client has too many recent failed attempts on the
    username and 401 when the credentials are invalid.
    """
    # /token takes OAuth2PasswordRequestForm, which has no length limits, so
    # enforce the signup limits here. Checked before the throttle so oversized
    # junk neither costs a hash nor fills the throttle's memory; no stored
    # account can match it anyway.
    if len(username) > USERNAME_MAX_LENGTH or len(password) > PASSWORD_MAX_LENGTH:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # The raw input is kept for the lookup, which tries the lenient and the
    # strict (NFKC) form; see find_climber_by_login_name.
    raw_username = username
    username = normalize_username(username)
    # Check again after normalizing: trimming can empty a username ("   "),
    # lowercasing can lengthen one ("İ" becomes two characters), and control
    # characters such as NUL make the database raise. Signup applies the same
    # rules to the normalized form, so no stored account matches.
    if not is_valid_normalized_username(username):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    # The throttle is keyed on the strict (NFKC) form: the lookup also matches
    # it, so "admin" and fullwidth or mixed variants of it all reach the same
    # account and must share one set of counters. Keyed on the lenient form,
    # each of the 2^n variants of an n-letter name would get its own limits.
    # Legacy names map to their NFKC key too, which is conservative (a legacy
    # decomposed "jose" and a new precomposed one share limits).
    throttle_key = normalize_new_username(raw_username)
    # Reserved up front so concurrent guesses cannot all pass the check; it
    # stays recorded as a failure for this client unless the login succeeds.
    reservation = login_throttle.acquire(throttle_key, client_address)
    if isinstance(reservation, int):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=TOO_MANY_REQUESTS_DETAIL,
            headers={"Retry-After": str(reservation)},
        )

    try:
        user = await _check_credentials(session, raw_username, password)
    except Exception:
        # A database or hashing error is not a failed guess, so hand back this
        # request's reservation. asyncio.CancelledError is a BaseException and
        # deliberately not caught: a cancelled attempt stays counted, which
        # errs on the safe side.
        login_throttle.release(reservation)
        raise
    if user is None:
        login_throttle.failed(reservation)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    login_throttle.succeeded(reservation)

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
    user = await authenticate_user(session, body.username, body.password, client_ip(request))
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
    user = await authenticate_user(
        session, form_data.username, form_data.password, client_ip(request)
    )
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
    username = normalize_username(body.username)
    # No account can have such a name, and NUL would make the database raise;
    # answer exactly as for an unknown username.
    if not is_valid_normalized_username(username):
        return MessageResponse(message=PASSWORD_RESET_REQUESTED_MESSAGE)
    user = await find_climber_by_login_name(session, body.username)

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
    return MessageResponse(message=PASSWORD_RESET_REQUESTED_MESSAGE)


@router.post("/password-reset/confirm", response_model=MessageResponse, responses=RATE_LIMITED_RESPONSE)
@password_reset_confirm_rate_limit
async def confirm_password_reset(request: Request, body: PasswordResetConfirm, session: Session):
    # No issued token contains control characters or lone surrogates, and
    # either would make the database raise: answer as for any invalid token,
    # without a query.
    if contains_unstorable_characters(body.token):
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
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
