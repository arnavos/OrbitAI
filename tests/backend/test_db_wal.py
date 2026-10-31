"""Tests validating SQLite WAL mode, PRAGMA parameters, and transaction isolation."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.system import SystemSetting
from tests.backend.conftest import test_engine


@pytest.mark.asyncio
async def test_sqlite_wal_pragmas_active(setup_test_database):
    """Verify that all required SQLite performance and concurrency PRAGMAs are strictly set."""
    async with test_engine.connect() as conn:
        res_jm = await conn.execute(text("PRAGMA journal_mode;"))
        journal_mode = res_jm.scalar()
        assert str(journal_mode).lower() == "wal", f"Expected WAL mode, got {journal_mode}"

        res_sync = await conn.execute(text("PRAGMA synchronous;"))
        synchronous = res_sync.scalar()
        # 1 corresponds to NORMAL
        assert synchronous == 1, f"Expected synchronous=1 (NORMAL), got {synchronous}"

        res_fk = await conn.execute(text("PRAGMA foreign_keys;"))
        foreign_keys = res_fk.scalar()
        assert foreign_keys == 1, f"Expected foreign_keys=1 (ON), got {foreign_keys}"

        res_bt = await conn.execute(text("PRAGMA busy_timeout;"))
        busy_timeout = res_bt.scalar()
        assert busy_timeout == 5000, f"Expected busy_timeout=5000, got {busy_timeout}"


@pytest.mark.asyncio
async def test_transaction_rollback_on_failure(db_session: AsyncSession):
    """Verify that transactions cleanly roll back when an exception occurs."""
    setting = SystemSetting(key="rollback_test_key", value_json='{"state": "temporary"}')
    db_session.add(setting)
    await db_session.flush()

    # Roll back
    await db_session.rollback()

    # Query again
    res = await db_session.execute(text("SELECT key FROM system_settings WHERE key = 'rollback_test_key';"))
    assert res.scalar_one_or_none() is None
