from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.problems import Problem
from models.problem_attempts import ProblemAttempt
from models.climbers import Climber
from models.participation import Participation
from pydantic import BaseModel

attempts_router = APIRouter(prefix="/attempts", tags=["Problem Attempts"])

class AttemptData(BaseModel):
    climber_id: str
    problem_id: int
    attempts: int = 0
    top: int = 0
    bonus: int = 0

@attempts_router.post("/")
def save_attempt(data: AttemptData, db: Session = Depends(get_db)):
    climber = db.query(Climber).get(data.climber_id)
    if not climber:
        raise HTTPException(status_code=404, detail="Climber not found")

    problem = db.query(Problem).get(data.problem_id)
    if not problem:
        raise HTTPException(status_code=404, detail="Problem not found")

    competition_id = problem.competition_id

    attempt = (
        db.query(ProblemAttempt)
        .filter_by(
            climber_id=data.climber_id,
            problem_id=data.problem_id,
            competition_id=competition_id,
        )
        .first()
    )

    if attempt:
        attempt.attempts = data.attempts
        attempt.top = data.top
        attempt.bonus = data.bonus
    else:
        attempt = ProblemAttempt(
            climber_id=data.climber_id,
            problem_id=data.problem_id,
            competition_id=competition_id,
            attempts=data.attempts,
            top=data.top,
            bonus=data.bonus,
        )
        db.add(attempt)

    db.commit()
    db.refresh(attempt)

    return {
        "message": f"Attempt recorded for {climber.name} on {problem.name}",
        "competition_id": competition_id,
        "climber_id": data.climber_id,
        "problem_id": data.problem_id,
        "attempts": attempt.attempts,
        "top": attempt.top,
        "bonus": attempt.bonus,
    }

@attempts_router.get("/climber/{climber_id}/competition/{competition_id}")
def get_climber_attempts_for_competition(climber_id: str, competition_id: int, db: Session = Depends(get_db)):
    """
    Return all problems for a competition with the climber's attempts, tops, and bonuses.
    """

    # 1️⃣ Validate climber and competition participation
    climber = db.query(Climber).get(climber_id)
    if not climber:
        raise HTTPException(404, "Climber not found")

    participation = (
        db.query(Participation)
        .filter_by(climber_id=climber_id, competition_id=competition_id)
        .first()
    )
    if not participation:
        raise HTTPException(404, "Climber has not joined this competition")

    # 2️⃣ Get all problems for this competition and grade
    problems = (
        db.query(Problem)
        .filter_by(competition_id=competition_id, grade=participation.grade)
        .order_by(Problem.number)
        .all()
    )

    if not problems:
        raise HTTPException(404, "No problems found for this competition and grade")

    # 3️⃣ Get all attempts for this climber & competition
    attempts = (
        db.query(ProblemAttempt)
        .filter_by(climber_id=climber_id, competition_id=competition_id)
        .all()
    )

    attempts_by_problem = {a.problem_id: a for a in attempts}

    # 4️⃣ Combine problem and attempt info
    problem_data = []
    for p in problems:
        a = attempts_by_problem.get(p.id)
        problem_data.append({
            "id": p.id,
            "name": p.name,
            "number": p.number,
            "grade": p.grade,
            "top": a.top if a else 0,
            "bonus": a.bonus if a else 0,
            "attempts": a.attempts if a else 0,
        })

    # 5️⃣ Return structured response
    return {
        "competition_id": competition_id,
        "climber_id": climber_id,
        "grade": participation.grade,
        "problems": problem_data
    }