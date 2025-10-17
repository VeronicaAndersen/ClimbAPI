from pydantic import BaseModel
from typing import List, Optional
from datetime import date
from ClimbAPI.models.problem_attempts import ProblemAttempt

# ---- Base model ----
class CompetitionBase(BaseModel):
    id: int
    compname: str
    compdate: date
    visible: bool

    class Config:
        orm_mode = True


# ---- Create input ----
class CompetitionCreate(BaseModel):
    compname: str
    compdate: date
    visible: Optional[bool] = True


# ---- Update input ----
class CompetitionUpdate(BaseModel):
    compname: Optional[str] = None
    compdate: Optional[date] = None
    visible: Optional[bool] = None


# ---- Full details with problems ----
class CompetitionWithProblems(CompetitionBase):
    problems: List[ProblemAttempt] = []


# ---- Competition list response ----
class CompetitionList(BaseModel):
    competitions: List[CompetitionBase]
    total: int
