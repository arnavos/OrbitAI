"""Pytest test fixtures, in-memory database configuration, and async test client."""

import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure both repo root and backend root are on path
repo_dir = Path(__file__).resolve().parent.parent.parent
backend_dir = repo_dir / "backend"
if str(repo_dir) not in sys.path:
    sys.path.insert(0, str(repo_dir))
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Configure testing environment variables before importing app
TEST_SESSION_TOKEN = "test-secret-token-orbit-999"
os.environ["APP_ENV"] = "testing"
os.environ["ORBIT_SESSION_TOKEN"] = TEST_SESSION_TOKEN

from app.db.base import Base
from app.db.session import configure_sqlite_pragmas, get_db
from app.main import app

# Create a temporary file-based SQLite database for testing WAL pragmas
TEST_DB_PATH = backend_dir / "storage" / "test_orbit.db"
TEST_DATABASE_URL = f"sqlite+aiosqlite:///{TEST_DB_PATH}"

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
configure_sqlite_pragmas(test_engine)

TestingSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@pytest_asyncio.fixture
async def setup_test_database():
    """Create test database tables, triggers, and cleanup after each test."""
    TEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Apply append-only triggers to test database
        await conn.execute(
            text("""
            CREATE TRIGGER IF NOT EXISTS trg_audit_events_no_update
            BEFORE UPDATE ON audit_events
            BEGIN
                SELECT RAISE(ABORT, 'audit_events table is append-only: updates are prohibited');
            END;
            """)
        )
        await conn.execute(
            text("""
            CREATE TRIGGER IF NOT EXISTS trg_audit_events_no_delete
            BEFORE DELETE ON audit_events
            BEGIN
                SELECT RAISE(ABORT, 'audit_events table is append-only: deletions are prohibited');
            END;
            """)
        )
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(setup_test_database) -> AsyncGenerator[AsyncSession]:
    """Provide a clean transactional session for an individual test."""
    async with TestingSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def async_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """Provide an AsyncClient wired with the test database session and lifespan."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Provide valid authentication headers."""
    return {"x-orbit-session-token": TEST_SESSION_TOKEN}
