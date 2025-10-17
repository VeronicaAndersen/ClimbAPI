from pydantic import BaseModel

class ClimberRegister(BaseModel):
    name: str
    password: str

class ClimberOut(BaseModel):
    id: str
    name: str
    
    class Config:
        orm_mode = True

class ClimberLogin(BaseModel):
    name: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    climber_id: str 
    climber_name: str
