from datetime import datetime
from typing import Optional

from pydantic import BaseModel, constr

from schema.auth import TokenPair


class ClimberCreate(BaseModel):
    name: constr(min_length=1, max_length=200)
    password: constr(min_length=6, max_length=1024)


class ClimberUpdate(BaseModel):
    name: Optional[constr(min_length=1, max_length=200)] = None
    password: Optional[constr(min_length=6, max_length=1024)] = None
    user_scope: Optional[str] = None


class ClimberOut(BaseModel):
    id: int
    name: str
    user_scope: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AuthOut(TokenPair):
    climber: ClimberOut
