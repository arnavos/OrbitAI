"""Database engine creation, SQLite WAL pragma configuration, and session factories."""

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.logging import logger
from app.db.base import Base


def configure_sqlite_pragmas(db_engine: AsyncEngine) -> None:
    """Register connection listeners on SQLite engine to enforce WAL mode and pragmas."""

    @event.listens_for(db_engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        # Enable Write-Ahead Logging for high concurrency
        cursor.execute("PRAGMA journal_mode = WAL;")
        # Set synchronous to NORMAL (safe on SSDs with WAL, high write performance)
        cursor.execute("PRAGMA synchronous = NORMAL;")
        # Enforce foreign key constraints
        cursor.execute("PRAGMA foreign_keys = ON;")
        # Busy timeout in ms to prevent 'database is locked' errors
        cursor.execute("PRAGMA busy_timeout = 5000;")
        # Cache size in KiB (-64000 = ~64MB cache limit)
        cursor.execute("PRAGMA cache_size = -64000;")
        cursor.close()


def create_engine_and_sessionmaker(
    db_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create configured AsyncEngine and sessionmaker for the provided database URL."""
    is_sqlite = db_url.startswith("sqlite")

    engine = create_async_engine(
        db_url,
        echo=settings.debug,
        future=True,
    )

    if is_sqlite:
        configure_sqlite_pragmas(engine)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    return engine, session_factory


# Global application engine and sessionmaker
engine, async_session_factory = create_engine_and_sessionmaker(settings.database_url)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency yielding an async database session with automatic rollback on error."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Initialize database schema directly (used primarily for test fixtures and bootstrap)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialized successfully.")
