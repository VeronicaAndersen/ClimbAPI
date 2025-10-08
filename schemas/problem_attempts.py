from pydantic import BaseModel

class ProblemAttemptCreate(BaseModel):
    climber_id: str
    competition_id: int
    problem_id: int
    attempts: int
    bonus_attempt: int
    top_attempt: int

class ProblemAttemptOut(ProblemAttemptCreate):
    id: int

    class Config:
        orm_mode = True
