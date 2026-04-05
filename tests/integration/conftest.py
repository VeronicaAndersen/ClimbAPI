"""
Shared fixtures for integration tests.

Uses an in-memory SQLite database so no external PostgreSQL instance is needed.
Each test function gets a fresh database (function-scoped fixtures).
"""
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

from db.config import get_session
from db.models import Base
from main import app

# SQLite renders BigInteger as "BIGINT NOT NULL, PRIMARY KEY (id)" — a
# table-level constraint that does NOT trigger SQLite's rowid alias, so
# inserts fail with "NOT NULL constraint failed: *.id".  Rendering as
# "INTEGER" lets SQLite auto-assign the rowid for every primary-key column.
SQLiteTypeCompiler.visit_big_integer = lambda self, type_, **kw: "INTEGER"

# StaticPool forces a single underlying connection so all operations share
# the same in-memory SQLite database.  Without it each pool checkout opens a
# new :memory: connection (i.e. an empty database), causing IntegrityErrors.
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture()
async def engine():
    """Create a fresh in-memory SQLite engine with all tables."""
    _engine = create_async_engine(
        TEST_DB_URL,
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield _engine
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await _engine.dispose()


@pytest.fixture()
async def client(engine):
    """
    AsyncClient wired to the FastAPI app with get_session overridden
    to use the test SQLite database.
    """
    factory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    async def override_get_session():
        async with factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_session] = override_get_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c

    app.dependency_overrides.clear()
