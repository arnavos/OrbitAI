"""Tests verifying that AuditEvent records are strictly immutable and append-only."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.system import AuditEvent


@pytest.mark.asyncio
async def test_audit_event_insertion(db_session: AsyncSession):
    """Verify appending a new AuditEvent record succeeds."""
    event = AuditEvent(
        correlation_id="test-cid-100",
        event_type="SYSTEM_BOOT",
        actor="system",
        severity="INFO",
        payload_json='{"status": "ok"}',
    )
    db_session.add(event)
    await db_session.commit()
    await db_session.refresh(event)

    assert event.id is not None
    assert event.event_type == "SYSTEM_BOOT"


@pytest.mark.asyncio
async def test_audit_event_orm_update_prevented(db_session: AsyncSession):
    """Verify that updating an existing AuditEvent via SQLAlchemy ORM raises PermissionError."""
    event = AuditEvent(
        correlation_id="test-cid-200",
        event_type="CONFIG_CHANGE",
        actor="admin",
        severity="WARNING",
        payload_json='{"change": "initial"}',
    )
    db_session.add(event)
    await db_session.commit()

    # Attempt to modify the record through ORM
    event.severity = "CRITICAL"
    with pytest.raises(PermissionError) as exc_info:
        await db_session.commit()

    assert "append-only and cannot be updated" in str(exc_info.value)
    await db_session.rollback()


@pytest.mark.asyncio
async def test_audit_event_orm_delete_prevented(db_session: AsyncSession):
    """Verify that deleting an existing AuditEvent via SQLAlchemy ORM raises PermissionError."""
    event = AuditEvent(
        correlation_id="test-cid-300",
        event_type="SECURITY_ALERT",
        actor="guard",
        severity="CRITICAL",
        payload_json='{"alert": "tamper_test"}',
    )
    db_session.add(event)
    await db_session.commit()

    # Attempt to delete the record through ORM
    await db_session.delete(event)
    with pytest.raises(PermissionError) as exc_info:
        await db_session.commit()

    assert "append-only and cannot be deleted" in str(exc_info.value)
    await db_session.rollback()


@pytest.mark.asyncio
async def test_audit_event_database_trigger_blocks_raw_sql_update(db_session: AsyncSession):
    """Verify that even direct SQL UPDATE bypasses are blocked by SQLite database triggers."""
    event = AuditEvent(
        correlation_id="test-cid-400",
        event_type="DIRECT_SQL_TEST",
        actor="system",
        severity="INFO",
        payload_json='{"data": 1}',
    )
    db_session.add(event)
    await db_session.commit()

    # Attempt direct raw SQL UPDATE
    with pytest.raises(DBAPIError) as exc_info:
        await db_session.execute(
            text(f"UPDATE audit_events SET severity = 'HACKED' WHERE id = {event.id};")
        )
        await db_session.commit()

    assert "audit_events table is append-only: updates are prohibited" in str(exc_info.value)
    await db_session.rollback()


@pytest.mark.asyncio
async def test_audit_event_database_trigger_blocks_raw_sql_delete(db_session: AsyncSession):
    """Verify that direct raw SQL DELETE statements are blocked by SQLite database triggers."""
    event = AuditEvent(
        correlation_id="test-cid-500",
        event_type="DELETE_SQL_TEST",
        actor="system",
        severity="INFO",
        payload_json='{"data": 2}',
    )
    db_session.add(event)
    await db_session.commit()

    # Attempt direct raw SQL DELETE
    with pytest.raises(DBAPIError) as exc_info:
        await db_session.execute(
            text(f"DELETE FROM audit_events WHERE id = {event.id};")
        )
        await db_session.commit()

    assert "audit_events table is append-only: deletions are prohibited" in str(exc_info.value)
    await db_session.rollback()
