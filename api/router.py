from fastapi import APIRouter

from api.v1.router import api_router as v1_router

api_router = APIRouter(prefix="/api")

api_router.include_router(v1_router, prefix="/v1")
