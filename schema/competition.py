from datetime import date
from enum import Enum
from typing import Literal
from typing import Optional

from pydantic import BaseModel, conint, model_validator


class CompType(str, Enum):
    QUALIFIER = "QUALIFIER"
    FINAL = "FINAL"


class CompetitionCreate(BaseModel):
    name: str
    description: Optional[str] = None
    comp_type: CompType
    comp_date: date
    season_id: int
    round_no: Optional[conint(ge=1, le=3)] = None

    @model_validator(mode="after")
    def _check_round_vs_type(self):
        if self.comp_type == "QUALIFIER" and self.round_no is None:
            raise ValueError("Qualifier must have round_no 1..3")
        if self.comp_type == "FINAL" and self.round_no is not None:
            raise ValueError("Final must not have round_no")
        return self


class CompetitionUpdate(BaseModel):
    # All fields optional; we validate after merge in the router
    name: Optional[str] = None
    description: Optional[str] = None
    comp_type: Optional[CompType] = None
    comp_date: Optional[date] = None
    season_id: Optional[int] = None
    round_no: Optional[conint(ge=1, le=3)] = None


class CompetitionOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    comp_type: CompType
    comp_date: date
    season_id: int
    round_no: Optional[int]

    model_config = {"from_attributes": True}
