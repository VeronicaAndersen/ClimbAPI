from typing import Optional, List

from pydantic import BaseModel, ConfigDict, conint, model_validator
from pydantic import Field

ORMModel = ConfigDict(from_attributes=True)


class ProblemScoreUpsert(BaseModel):
    attempts_total: conint(ge=0)
    got_bonus: bool
    got_top: bool
    attempts_to_bonus: Optional[conint(ge=1)] = None
    attempts_to_top: Optional[conint(ge=1)] = None

    @model_validator(mode="after")
    def _rules(self):
        # If achieved, the corresponding attempts_to_* must be provided
        if self.got_bonus and self.attempts_to_bonus is None:
            raise ValueError("attempts_to_bonus is required when got_bonus is true")
        if self.got_top and self.attempts_to_top is None:
            raise ValueError("attempts_to_top is required when got_top is true")

        # attempts_to_* cannot exceed attempts_total
        if self.attempts_to_bonus is not None and self.attempts_to_bonus > self.attempts_total:
            raise ValueError("attempts_to_bonus cannot exceed attempts_total")
        if self.attempts_to_top is not None and self.attempts_to_top > self.attempts_total:
            raise ValueError("attempts_to_top cannot exceed attempts_total")

        # If Top is achieved, bonus must have been achieved earlier or same try
        if self.got_top:
            if not self.got_bonus:
                raise ValueError("IFSC: Top implies Zone (got_bonus must be true if got_top is true)")
            if self.attempts_to_bonus is not None and self.attempts_to_top is not None:
                if self.attempts_to_top < self.attempts_to_bonus:
                    raise ValueError("IFSC: attempts_to_top must be >= attempts_to_bonus")

        return self


class ProblemScoreBulkItem(ProblemScoreUpsert):
    problem_no: conint(ge=1, le=8)


class ProblemScoreBulkRequest(BaseModel):
    items: List[ProblemScoreBulkItem] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _unique_problems(self):
        nums = [i.problem_no for i in self.items]
        if len(nums) != len(set(nums)):
            raise ValueError("Duplicate problem_no in payload")
        return self


class ProblemScoreOutBulk(BaseModel):
    attempts_total: int
    got_bonus: bool
    got_top: bool
    attempts_to_bonus: Optional[int] = None
    attempts_to_top: Optional[int] = None
    model_config = {"from_attributes": True}


class ProblemScoreOut(ProblemScoreOutBulk):
    problem_no: int


class ProblemScoreBulkResult(BaseModel):
    problem_no: int
    score: ProblemScoreOutBulk