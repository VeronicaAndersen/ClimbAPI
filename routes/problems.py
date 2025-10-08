from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import problems as models, climbers as cm, competitions as comp, problem_attempts as pa
from models.participation import Participation  # ✅ Add this import
from schemas.problems import ProblemBase
from constants.grades import GRADES

problems_router = APIRouter(prefix="/problems", tags=["Problems"])

@problems_router.get("/climber/{climber_id}", response_model=list[ProblemBase])
def get_problems_for_climber(climber_id: str, competition_id: int, db: Session = Depends(get_db)):
    climber = db.query(cm.Climber).get(climber_id)
    if not climber:
        raise HTTPException(404, "Climber not found")

    # ✅ Correct model reference
    participation = (
        db.query(Participation)
        .filter_by(climber_id=climber_id, competition_id=competition_id)
        .first()
    )

    if not participation:
        raise HTTPException(404, "Climber has not joined this competition")

    # Get problems for the climber's grade
    problems = (
        db.query(models.Problem)
        .filter_by(competition_id=competition_id, grade=participation.grade)
        .all()
    )

    # If no problems exist yet, create them
    if not problems:
        problems_to_add = [
            models.Problem(
                name=f"{participation.grade} #{i}",
                number=i,
                grade=participation.grade,
                competition_id=competition_id,
                visible=True,
            )
            for i in range(1, 9)
        ]
        db.add_all(problems_to_add)
        db.commit()
        problems = (
            db.query(models.Problem)
            .filter_by(competition_id=competition_id, grade=participation.grade)
            .all()
        )

    return problems
