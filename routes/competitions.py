from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.competitions import Competition
from models.climbers import Climber
from models.problems import Problem
from models.participation import Participation
from schemas.problems import ProblemBase
from constants.grades import GRADES

competitions_router = APIRouter(prefix="/competitions", tags=["Competitions"])

# ---- List Competitions ----
@competitions_router.get("/")
def list_competitions(db: Session = Depends(get_db)):
    """
    List all competitions.
    """
    competitions = db.query(Competition).all()
    return [
        {
            "id": comp.id,
            "name": comp.compname,
            "date": str(comp.compdate),
            "participants": comp.comppart,
            "visible": comp.visible,
        }
        for comp in competitions
    ]

# ---- Create Competition ----
@competitions_router.post("/create")
def create_competition(
    compname: str,
    compdate: date,
    comppart: int = 0,
    db: Session = Depends(get_db),
):
    """
    Create a new competition.
    """
    # Check for duplicates
    existing = db.query(Competition).filter_by(compname=compname).first()
    if existing:
        raise HTTPException(status_code=400, detail="Competition with this name already exists")

    comp = Competition(compname=compname, compdate=compdate, comppart=comppart, visible=True)
    db.add(comp)
    db.commit()
    db.refresh(comp)

    return {
        "message": "Competition created successfully",
        "competition": {
            "id": comp.id,
            "name": comp.compname,
            "date": str(comp.compdate),
            "participants": comp.comppart,
            "visible": comp.visible,
        },
    }
    
@competitions_router.post("/join")
def join_competition(
    climber_id: str,
    competition_id: int,
    grade: str,
    db: Session = Depends(get_db),
):
    climber = db.query(Climber).get(climber_id)
    if not climber:
        raise HTTPException(status_code=404, detail="Climber not found")

    competition = db.query(Competition).get(competition_id)
    if not competition:
        raise HTTPException(status_code=404, detail="Competition not found")

    if grade not in GRADES:
        raise HTTPException(status_code=400, detail=f"Invalid grade. Valid: {', '.join(GRADES)}")

    participation = (
        db.query(Participation)
        .filter_by(climber_id=climber_id, competition_id=competition_id)
        .first()
    )

    if participation:
        participation.grade = grade
    else:
        participation = Participation(
            climber_id=climber_id, competition_id=competition_id, grade=grade
        )
        db.add(participation)
        # ✅ Update participant count
        competition.comppart = (competition.comppart or 0) + 1

    db.flush()

    existing = (
        db.query(Problem)
        .filter_by(competition_id=competition_id, grade=grade)
        .order_by(Problem.number)
        .all()
    )

    if not existing:
        new_problems = [
            Problem(
                number=i,
                name=f"{grade} #{i}",
                grade=grade,
                visible=True,
                competition_id=competition_id,
            )
            for i in range(1, 9)
        ]
        db.add_all(new_problems)
        existing = new_problems

    db.commit()

    return {
        "message": f"{climber.name} joined {competition.compname} as grade {grade}",
        "competition": {
            "id": competition.id,
            "name": competition.compname,
            "date": str(competition.compdate),
            "participants": competition.comppart,
        },
        "climber": {
            "id": climber.id,
            "name": climber.name,
            "grade": grade,
        },
        "problems": [ProblemBase.from_orm(p) for p in existing],
    }
