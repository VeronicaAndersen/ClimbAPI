from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
import os, re
from contextlib import asynccontextmanager
from typing import AsyncIterator

DATABASE_URL = os.getenv("ASYNC_DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

if not re.match(r"^postgresql\+(asyncpg|psycopg)://", DATABASE_URL):
    raise RuntimeError("DATABASE_URL must use an async driver")

@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with async_session() as session:
        try:
            yield session
        finally:
            await engine.dispose()
