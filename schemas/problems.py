from pydantic import BaseModel, ConfigDict

class ProblemBase(BaseModel):
    id: int | None = None
    number: int
    name: str
    grade: str
    visible: bool
    competition_id: int

    # Allow ORM conversion
    model_config = ConfigDict(from_attributes=True)
