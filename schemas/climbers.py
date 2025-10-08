# schemas/climbers.py
from pydantic import BaseModel

class ClimberRegister(BaseModel):
    name: str
    selected_grade: str
    password: str

class ClimberOut(BaseModel):
    id: str
    name: str
    selected_grade: str

    class Config:
        orm_mode = True

class ClimberLogin(BaseModel):
    name: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    climber_id: str 
