from pydantic import BaseModel


class LeaderboardEntry(BaseModel):
    rank: int
    climber_id: int
    climber_name: str
    total_score: float
    tops: int
    bonuses: int
    attempts_to_top: int
    attempts_to_bonus: int

    model_config = {"from_attributes": True}


class LevelLeaderboard(BaseModel):
    competition_id: int
    level: int
    entries: list[LeaderboardEntry]

    model_config = {"from_attributes": True}
