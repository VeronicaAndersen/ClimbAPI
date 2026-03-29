from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.router import api_router


app = FastAPI(title="Grepp API (FastAPI Edition)",
    version="1.0.0",
    swagger_ui_parameters={
        "persistAuthorization": True,
    },
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://grepp.vercel.app", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, _exc: Exception):
    """Catch-all so unhandled 500s still pass through CORSMiddleware."""
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8080)
