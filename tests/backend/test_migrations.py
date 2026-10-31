"""Tests verifying Alembic migrations, upgrade, and downgrade rollback."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent / "backend"
ALEMBIC_INI_PATH = BACKEND_DIR / "alembic.ini"
MIGRATION_TEST_DB = BACKEND_DIR / "storage" / "migration_test.db"


@pytest.fixture
def alembic_config():
    """Create Alembic configuration pointing to a dedicated test database."""
    MIGRATION_TEST_DB.parent.mkdir(parents=True, exist_ok=True)
    if MIGRATION_TEST_DB.exists():
        MIGRATION_TEST_DB.unlink()

    cfg = Config(str(ALEMBIC_INI_PATH))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{MIGRATION_TEST_DB}")
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    yield cfg

    if MIGRATION_TEST_DB.exists():
        try:
            MIGRATION_TEST_DB.unlink()
        except OSError:
            pass


def test_migration_upgrade_and_rollback(alembic_config: Config):
    """Verify that migrations upgrade to head, rollback to base, and upgrade again seamlessly."""
    sync_engine = create_engine(f"sqlite:///{MIGRATION_TEST_DB}")

    # 1. Run upgrade head
    command.upgrade(alembic_config, "head")

    # Verify tables exist
    inspector = inspect(sync_engine)
    tables = inspector.get_table_names()
    assert "system_settings" in tables
    assert "audit_events" in tables
    assert "health_snapshots" in tables

    # 2. Run rollback to base
    command.downgrade(alembic_config, "base")

    # Verify tables removed
    inspector = inspect(sync_engine)
    tables_after_downgrade = inspector.get_table_names()
    assert "system_settings" not in tables_after_downgrade
    assert "audit_events" not in tables_after_downgrade
    assert "health_snapshots" not in tables_after_downgrade

    # 3. Upgrade back to head
    command.upgrade(alembic_config, "head")
    inspector = inspect(sync_engine)
    assert "audit_events" in inspector.get_table_names()

    sync_engine.dispose()
