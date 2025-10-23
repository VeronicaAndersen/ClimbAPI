from typing import Annotated
from enum import Enum
from fastapi import Depends, HTTPException, status, Security
from fastapi.security import OAuth2PasswordBearer, SecurityScopes
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Iterable, Set, Dict

from db.config import get_session
from db.models import Climber
from security.jwt_tools import decode_token

# oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# async def get_current_user(token: str = Depends(oauth2), session: AsyncSession = Depends(get_session)) -> Climber:
#     try:
#         payload = decode_token(token)
#     except Exception:
#         raise HTTPException(status_code=401, detail="Invalid or expired token")
#     if payload.get("type") != "access":
#         raise HTTPException(status_code=401, detail="Wrong token type")
#     uid = int(payload["sub"])
#     user = await session.get(Climber, uid)
#     if not user:
#         raise HTTPException(status_code=401, detail="User not found")
#     return user
#
# CurrentUser = Annotated[Climber, Depends(get_current_user)]

# def require_admin(user: Climber = Depends(get_current_user)) -> Climber:
#     if not getattr(user, "is_admin", False):
#         raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
#     return user
#
# AdminUser   = Annotated[Climber, Depends(require_admin)]

ALL_SCOPES = {"climber", "setter", "analyst", "admin"}

ROLE_HIERARCHY: Dict[str, Set[str]] = {
    "climber": {"climber"},
    "setter": {"setter", "climber"},
    "analyst": {"analyst", "climber"},
    "admin": {"admin", "analyst", "setter", "climber"},
}

oauth2 = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    scopes={s: f"{s} access" for s in ALL_SCOPES},  # docs + OpenAPI
)


def expand_scopes(role: Enum) -> Set[str]:
    effective: Set[str] = set()
    role = str(role.value).lower()
    effective |= ROLE_HIERARCHY.get(role, set())
    return effective


async def get_current_user(security_scopes: SecurityScopes,
                           token: str = Depends(oauth2),
                           session: AsyncSession = Depends(get_session)
                           ) -> Climber:
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

    user_effective = expand_scopes(getattr(user, "user_scope", []) or [])

    # enforce required scopes if any were requested
    required = set(security_scopes.scopes)
    if required and not required.issubset(user_effective):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions"
        )

    # stash for later
    user.effective_scopes = user_effective
    return user

def Require(*scopes: str):
    """Use like: user: CurrentUser = Security(get_current_user, scopes=['admin'])"""
    return Security(get_current_user, scopes=list(scopes))

CurrentUser = Annotated[Climber, Security(get_current_user)]
AdminUser = Annotated[Climber, Security(get_current_user, scopes=["admin"])]