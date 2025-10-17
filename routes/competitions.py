from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.competitions import Competition
from models.climbers import Climber
from models.problem_attempts import ProblemAttempt
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
            "visible": comp.visible,
        }
        for comp in competitions
    ]

# ---- Create Competition ----
@competitions_router.post("/create")
def create_competition(
    compname: str,
    compdate: date,
    db: Session = Depends(get_db),
):
    """
    Create a new competition.
    """
    # Check for duplicates
    existing = db.query(Competition).filter_by(compname=compname, compdate=compdate).first()
    if existing:
        raise HTTPException(status_code=400, detail="Competition already exists")

    comp = Competition(compname=compname, compdate=compdate, visible=True)
    db.add(comp)
    db.commit()
    db.refresh(comp)

    return {
        "message": "Competition created successfully",
        "competition": {
            "id": comp.id,
            "name": comp.compname,
            "date": str(comp.compdate),
            "visible": comp.visible,
        },
    }
    
# climber joins a competition by id and choosing a grade
@competitions_router.post("/{competition_id}/join/{climber_id}")
def join_competition(
    competition_id: int,
    climber_id: str,
    selected_grade: str,
    db: Session = Depends(get_db),
):
    competition = db.query(Competition).get(competition_id)
    if not competition:
        raise HTTPException(status_code=404, detail="Competition not found")
    
    climber = db.query(Climber).get(climber_id)
    if not climber:
        raise HTTPException(status_code=404, detail="Climber not found")
    
    if selected_grade not in GRADES:
        raise HTTPException(status_code=400, detail="Invalid grade selected")
    
    # Check if the climber has already joined
    existing_attempts = (
        db.query(ProblemAttempt)
        .filter_by(climber_id=climber_id, competition_id=competition_id)
        .all()
    )
    if existing_attempts:
        raise HTTPException(status_code=400, detail="Climber has already joined this competition")
    
    # Create initial problem attempts for the climber in this competition
    problems = db.query(ProblemAttempt).filter_by(competition_id=competition_id).all()
    for problem in problems:
        attempt = ProblemAttempt(
            name=problem.name,
            climber_id=climber_id,
            competition_id=competition_id,
            attempts=0,
            top=0,
            bonus=0,
        )
        db.add(attempt)
    
    db.commit()
    
    return {
        "message": f"Climber {climber.name} joined competition {competition.compname} with grade {selected_grade}"
    }

# update competition info
@competitions_router.put("/{competition_id}")
def update_competition(
    competition_id: int,
    compname: str,
    compdate: date,
    visible: bool,
    db: Session = Depends(get_db),
):
    competition = db.query(Competition).get(competition_id)
    
    if not competition:
        raise HTTPException(status_code=404, detail="Competition not found")
    
    competition.compname = compname
    competition.compdate = compdate
    competition.visible = visible
    
    db.commit()
    db.refresh(competition)
    
    return {
        "id": competition.id,
        "name": competition.compname,
        "date": str(competition.compdate),
        "visible": competition.visible,
    }