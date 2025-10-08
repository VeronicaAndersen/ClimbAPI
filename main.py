from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import Base, engine
from routes.problems import problems_router
from routes.problem_attempts import attempts_router
from routes.competitions import competitions_router
from routes.climbers import climbers_router
from routes.grades import grades_router

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Grepp API (FastAPI Edition)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(problems_router)
app.include_router(attempts_router)
app.include_router(competitions_router)
app.include_router(climbers_router)
app.include_router(grades_router)
