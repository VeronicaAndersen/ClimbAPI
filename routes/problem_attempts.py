from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from models.competitions import Competition
from schemas.problem_attempts import ProblemAttemptCreate
from database import get_db
from models.problem_attempts import ProblemAttempt, Problem
from models.climbers import Climber

attempts_router = APIRouter(prefix="/attempts", tags=["Problem Attempts"])


@attempts_router.post("/climber/{climber_id}/competition/{competition_id}/save")
def save_attempt(
    climber_id: str,
    competition_id: int,
    data: ProblemAttemptCreate,
    db: Session = Depends(get_db),
):
    """
    Create or update a climber's attempt for a specific problem in a competition.
    """

    # Validate climber
    climber = db.query(Climber).get(climber_id)
    if not climber:
        raise HTTPException(status_code=404, detail="Climber not found")

    # Validate competition
    competition = db.query(Competition).get(competition_id)
    if not competition:
        raise HTTPException(status_code=404, detail="Competition not found")

    # Validate problem
    problem = db.query(Problem).get(data.problem_id)
    if not problem:
        raise HTTPException(status_code=404, detail="Problem not found")

    # Check if an attempt already exists
    attempt = (
        db.query(ProblemAttempt)
        .filter_by(
            climber_id=climber_id,
            competition_id=competition_id,
            problem_id=data.problem_id,
        )
        .first()
    )

    # Create or update the attempt
    if not attempt:
        attempt = ProblemAttempt(
            climber_id=climber_id,
            competition_id=competition_id,
            problem_id=data.problem_id,
            attempts=data.attempts or 0,
            bonus=data.bonus or 0,
            top=data.top or 0,
        )
        db.add(attempt)
    else:
        # Only update fields provided in the request
        if data.attempts is not None:
            attempt.attempts = data.attempts
        if data.bonus is not None:
            attempt.bonus = data.bonus
        if data.top is not None:
            attempt.top = data.top

    db.commit()
    db.refresh(attempt)

    return {
        "message": f"Attempt recorded for {climber.name} on {problem.name} (grade {problem.grade}) in competition {competition_id}.",
        "competition_id": competition_id,
        "climber_id": climber_id,
        "problem_id": data.problem_id,
        "attempts": attempt.attempts,
        "bonus": attempt.bonus,
        "top": attempt.top,
    }


@attempts_router.get("/climber/{climber_id}/competition/{competition_id}")
def get_climber_attempts_for_competition(
    climber_id: str,
    competition_id: int,
    selected_grade: str = Query(..., description="Selected grade to fetch problems for"),
    db: Session = Depends(get_db),
):
    """
    Return all problems for a competition with the climber's attempts, tops, and bonuses.
    If no problems exist for the grade, create 8 numbered problems automatically.
    """

    # Validate climber and competition 
    climber = db.query(Climber).get(climber_id)
    if not climber:
        raise HTTPException(status_code=404, detail="Climber not found")
    
    competition = db.query(Competition).get(competition_id)
    if not competition:
        raise HTTPException(status_code=404, detail="Competition not found")

    # Fetch problems for this grade
    problems = db.query(Problem).filter(Problem.grade == selected_grade).all()

    # If no problems exist — create 8 of them automatically
    if not problems:
        new_problems = []
        for i in range(1, 9):  # 1 through 8
            p = Problem(name=f"Problem {i}", grade=selected_grade)
            db.add(p)
            new_problems.append(p)
        db.commit()
        for p in new_problems:
            db.refresh(p)
        problems = new_problems

    # Fetch all attempts for this climber and competition
    attempts = (
        db.query(ProblemAttempt)
        .filter_by(climber_id=climber_id, competition_id=competition_id)
        .all()
    )

    # Map attempts by problem_id
    attempts_map = {a.problem_id: a for a in attempts}

    # Build structured response
    result = []
    for problem in problems:
        attempt = attempts_map.get(problem.id)
        result.append({
            "problem_id": problem.id,
            "problem_name": problem.name,
            "grade": problem.grade,
            "attempts": attempt.attempts if attempt else 0,
            "bonus": attempt.bonus if attempt else 0,
            "top": attempt.top if attempt else 0,
        })

    return {
        "competition_id": competition_id,
        "climber_id": climber_id,
        "grade": selected_grade,
        "problems": result,
    }
