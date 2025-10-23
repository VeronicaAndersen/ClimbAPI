from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Climber
from schema.auth import TokenPair, LoginRequest, RefreshRequest
from security.hashing import verify_password, needs_rehash, hash_password
from security.jwt_tools import create_access_token, create_refresh_token, decode_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)):
    user = await session.scalar(select(Climber).where(Climber.name == body.username))
    if not user or not verify_password(body.password, user.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Opportunistic rehash if parameters changed
    if needs_rehash(user.password):
        user.password = hash_password(body.password)

    return TokenPair(
        access_token=create_access_token(user.id, extra={"name": user.name}),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest):
    try:
        payload = decode_token(body.refresh_token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Wrong token type")
    sub = payload["sub"]
    return TokenPair(
        access_token=create_access_token(sub),
        refresh_token=create_refresh_token(sub),  # rotation; add blacklist if needed
    )
