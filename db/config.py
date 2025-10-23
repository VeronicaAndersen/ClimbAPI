import os
import re
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DATABASE_URL = os.getenv("ASYNC_DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

if not re.match(r"^postgresql\+(asyncpg|psycopg)://", DATABASE_URL):
    raise RuntimeError(
        "DATABASE_URL must use an async driver, e.g. "
        "'postgresql+asyncpg://user:pass@host/db' "
        "or 'postgresql+psycopg://user:pass@host/db'"
    )

engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,  # 30 min
    echo=False,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def lifespan_context(app):
    try:
        yield
    finally:
        # Ensure the pool is closed on shutdown
        await engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except:
            await session.rollback()
            raise
