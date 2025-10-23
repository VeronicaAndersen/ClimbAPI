from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from db.config import get_session
from db.models import Climber
from security.jwt_tools import decode_token

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

async def get_current_user(token: str = Depends(oauth2), session: AsyncSession = Depends(get_session)) -> Climber:
    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Wrong token type")
    uid = int(payload["sub"])
    user = await session.get(Climber, uid)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

CurrentUser = Annotated[Climber, Depends(get_current_user)]


def require_admin(user: Climber = Depends(get_current_user)) -> Climber:
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user

AdminUser   = Annotated[Climber, Depends(require_admin)]