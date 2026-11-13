import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from api.router import api_router
from security.hashing import prepare_dummy_hash
from services.email import warn_if_mail_not_configured

logger = logging.getLogger(__name__)

# Long enough to identify a query, short enough to keep log lines readable.
_MAX_LOGGED_STATEMENT = 1000


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    warn_if_mail_not_configured()
    await prepare_dummy_hash()
    yield


app = FastAPI(title="Grepp API (FastAPI Edition)",
    version="1.0.0",
    lifespan=lifespan,
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
    # Lets the browser app read how long to wait after a 429.
    expose_headers=["Retry-After"],
)

app.include_router(api_router)


def _printable(value: object) -> object:
    """Strings made safe to encode as UTF-8 (lone surrogates become "?")."""
    if isinstance(value, str):
        return value.encode("utf-8", "replace").decode("utf-8")
    return value


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    """
    422 with each error's loc, msg and type only.

    FastAPI's default also returns "input" (and "ctx"), which echoes request
    data such as a password back in the response. With a lone surrogate in
    the input (JSON "\\ud800") the default response could not even be encoded,
    giving a 500 and a traceback holding the request data.
    """
    detail = [
        {
            "loc": [_printable(part) for part in error.get("loc", ())],
            "msg": _printable(str(error.get("msg", ""))),
            "type": error.get("type"),
        }
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(DBAPIError)
async def database_error_handler(request: Request, exc: DBAPIError):
    """
    Generic 500 for database errors, logged without their message.

    Driver messages carry row data even with hide_parameters, e.g. asyncpg's
    DETAIL "Key (username)=(alice) already exists." or "Failing row contains
    (...)", which can include a password hash. Only the driver error type,
    SQLSTATE and the SQL statement are logged. The statement has placeholders
    ($1, :name) and never the bound values, because the app builds queries
    through the ORM; it is capped in length. Handled here rather than by the catch-all below,
    because Starlette re-raises catch-all errors after responding and the
    server would then log the full message. Routers that catch
    IntegrityError themselves are not affected.
    """
    statement = exc.statement or ""
    if len(statement) > _MAX_LOGGED_STATEMENT:
        statement = statement[:_MAX_LOGGED_STATEMENT] + " ..."
    logger.error(
        "Database error %s (sqlstate=%s) on %s %s; statement: %s",
        type(exc.orig).__name__,
        getattr(exc.orig, "sqlstate", None),
        request.method,
        request.url.path,
        statement,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


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
