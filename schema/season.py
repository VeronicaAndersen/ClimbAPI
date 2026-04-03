from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SeasonCreate(BaseModel):
    name: str
    year: int


class SeasonUpdate(BaseModel):
    # All fields optional; we validate after merge in the router
    name: Optional[str] = None
    year: Optional[int] = None


class SeasonOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    year: int

    model_config = {"from_attributes": True}


class SeasonStandingsEntry(BaseModel):
    rank: int
    name: str
    total_score: float


class LevelStandings(BaseModel):
    level: int
    entries: list[SeasonStandingsEntry]


class SeasonStandingsResponse(BaseModel):
    season_id: int
    season_name: str
    levels: list[LevelStandings]
