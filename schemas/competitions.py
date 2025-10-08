from pydantic import BaseModel
from typing import List, Optional
from datetime import date
from schemas.problems import ProblemBase

# ---- Base model ----
class CompetitionBase(BaseModel):
    id: int
    compname: str
    compdate: date
    comppart: int
    visible: bool

    class Config:
        orm_mode = True


# ---- Create input ----
class CompetitionCreate(BaseModel):
    compname: str
    compdate: date
    comppart: Optional[int] = 0
    visible: Optional[bool] = True


# ---- Update input ----
class CompetitionUpdate(BaseModel):
    compname: Optional[str] = None
    compdate: Optional[date] = None
    comppart: Optional[int] = None
    visible: Optional[bool] = None


# ---- Full details with problems ----
class CompetitionWithProblems(CompetitionBase):
    problems: List[ProblemBase] = []


# ---- Competition list response ----
class CompetitionList(BaseModel):
    competitions: List[CompetitionBase]
    total: int
