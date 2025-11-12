from ClimbAPI.db.config import engine
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.router import api_router


app = FastAPI(title="Grepp API (FastAPI Edition)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://grepp.vercel.app", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["*"],
)

app.include_router(api_router)

@app.on_event("shutdown")
async def shutdown():
    await engine.dispose()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8080)
