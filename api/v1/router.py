from fastapi import APIRouter

from api.v1.auth import router as auth_router
from api.v1.climber import router as climber_router
from api.v1.competition import router as comp_router
from api.v1.registration import router as reg_router
from api.v1.season import router as season_router
from api.v1.problem_score import router as problem_score_router

api_router = APIRouter()

api_router.include_router(auth_router)
api_router.include_router(climber_router)
api_router.include_router(comp_router)
api_router.include_router(reg_router)
api_router.include_router(season_router)
api_router.include_router(problem_score_router)

