from fastapi import APIRouter
from constants.grades import GRADES

grades_router = APIRouter(prefix="/grades", tags=["Grades"])

@grades_router.get("/", summary="List climbing grades")
def list_grades():
    """Get the list of climbing grades"""
    return {"grades": GRADES}
