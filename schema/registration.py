from datetime import datetime

from pydantic import BaseModel, conint


class RegistrationCreate(BaseModel):
    level: conint(ge=1, le=7)


class RegistrationOut(BaseModel):
    comp_id: int
    user_id: int
    level: int
    created_at: datetime

    model_config = {"from_attributes": True}
