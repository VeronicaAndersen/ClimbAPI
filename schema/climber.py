from datetime import datetime

from pydantic import BaseModel, constr

from schema.auth import TokenPair


class ClimberCreate(BaseModel):
    name: constr(min_length=1, max_length=200)
    password: constr(min_length=6, max_length=1024)


class ClimberOut(BaseModel):
    id: int
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AuthOut(TokenPair):
    climber: ClimberOut
