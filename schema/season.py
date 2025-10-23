from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SeasonCreate(BaseModel):
    name: str
    year: str


class SeasonUpdate(BaseModel):
    # All fields optional; we validate after merge in the router
    name: Optional[str] = None
    year: Optional[str] = None


class SeasonOut(BaseModel):
    id: int
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}
