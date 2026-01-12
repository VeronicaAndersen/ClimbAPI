from datetime import datetime

from pydantic import BaseModel, conint


class RegistrationCreate(BaseModel):
    level: conint(ge=1, le=7)


class RegistrationOut(BaseModel):
    comp_id: int
    user_id: int
    level: int
    approved: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RegistrationWithClimberOut(BaseModel):
    comp_id: int
    user_id: int
    level: int
    approved: bool
    created_at: datetime
    climber_name: str

    model_config = {"from_attributes": True}


class RegistrationApprovalUpdate(BaseModel):
    approved: bool
