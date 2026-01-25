from datetime import datetime
from typing import Optional

from pydantic import BaseModel, constr

from schema.auth import TokenPair


class ClimberCreate(BaseModel):
    username: constr(min_length=1, max_length=200)
    password: constr(min_length=6, max_length=1024)
    email: Optional[str] = None
    firstname: str
    lastname: str
    club: Optional[str] = None


class ClimberUpdate(BaseModel):
    username: Optional[constr(min_length=1, max_length=200)] = None
    password: Optional[constr(min_length=6, max_length=1024)] = None
    email: Optional[str] = None
    firstname: Optional[str] = None
    lastname: Optional[str] = None
    club: Optional[str] = None
    user_scope: Optional[str] = None


class ClimberOut(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    firstname: Optional[str] = None
    lastname: Optional[str] = None
    club: Optional[str] = None
    user_scope: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AuthOut(TokenPair):
    climber: ClimberOut
