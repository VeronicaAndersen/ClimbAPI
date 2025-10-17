from pydantic import BaseModel
from typing import Optional

class ProblemAttemptCreate(BaseModel):
    problem_id: int
    attempts: Optional[int] = 0
    bonus: Optional[int] = 0
    top: Optional[int] = 0