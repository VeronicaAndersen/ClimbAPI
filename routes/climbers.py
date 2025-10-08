from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from jose import jwt
from datetime import datetime, timedelta
from database import get_db
from models.climbers import Climber
from schemas.climbers import ClimberRegister, ClimberOut, ClimberLogin, TokenResponse

climbers_router = APIRouter(prefix="/climbers", tags=["Climbers"])

SECRET_KEY = "supersecretkey"  # change in production
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


@climbers_router.post("/register", response_model=ClimberOut)
def register_climber(data: ClimberRegister, db: Session = Depends(get_db)):
    existing = db.query(Climber).filter_by(name=data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Climber already exists")

    climber = Climber(name=data.name, selected_grade=data.selected_grade)
    climber.set_password(data.password)
    db.add(climber)
    db.commit()
    db.refresh(climber)
    return climber


@climbers_router.post("/login", response_model=TokenResponse)
def login_climber(data: ClimberLogin, db: Session = Depends(get_db)):
    climber = db.query(Climber).filter_by(name=data.name).first()
    if not climber or not climber.verify_password(data.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token({"sub": climber.id, "name": climber.name})
    return {"access_token": token, "token_type": "bearer", "climber_id": climber.id}


@climbers_router.get("/{climber_id}", response_model=ClimberOut)
def get_climber(climber_id: str, db: Session = Depends(get_db)):
    climber = db.query(Climber).get(climber_id)
    if not climber:
        raise HTTPException(status_code=404, detail="Climber not found")
    return climber

@climbers_router.get("/", response_model=list[ClimberOut])
def list_climbers(db: Session = Depends(get_db)):
    climbers = db.query(Climber).all()
    return climbers