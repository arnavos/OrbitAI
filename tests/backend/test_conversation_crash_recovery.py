"""Tests for crash recovery reconciliation of dangling in-flight turns."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Message
from app.schemas.conversation import ConversationCreate
from app.services.conversation_service import ConversationService


@pytest.mark.asyncio
async def test_reconcile_crashed_generations(db_session: AsyncSession):
    """Verify in-flight streaming/pending messages are marked interrupted on crash recovery."""
    conv = await ConversationService.create_conversation(
        session=db_session, payload=ConversationCreate(title="Crash Recovery Thread")
    )

    # 1. Setup messages with various statuses
    m_completed = Message(
        id="m-completed-id",
        conversation_id=conv.id,
        sequence=1,
        role="user",
        content="Hello turn 1",
        status="completed",
        finish_reason="stop",
    )
    m_streaming = Message(
        id="m-streaming-id",
        conversation_id=conv.id,
        sequence=2,
        role="assistant",
        content="Partial text before power outage...",
        status="streaming",  # Left dangling!
        finish_reason=None,
    )
    m_pending = Message(
        id="m-pending-id",
        conversation_id=conv.id,
        sequence=3,
        role="assistant",
        content="",
        status="pending",  # Left dangling!
        finish_reason=None,
    )
    m_failed = Message(
        id="m-failed-id",
        conversation_id=conv.id,
        sequence=4,
        role="assistant",
        content="Failed turn",
        status="failed",
        finish_reason="error",
        error_message="Original error",
    )

    db_session.add_all([m_completed, m_streaming, m_pending, m_failed])
    await db_session.commit()

    # 2. Run crash recovery reconciliation
    reconciled_count = await ConversationService.reconcile_crashed_generations(db_session)
    assert reconciled_count == 2  # exactly m_streaming and m_pending

    # 3. Verify state of all messages
    stmt = (
        select(Message).where(Message.conversation_id == conv.id).order_by(Message.sequence.asc())
    )
    res = await db_session.execute(stmt)
    messages = {m.id: m for m in res.scalars().all()}

    # Completed message untouched
    assert messages["m-completed-id"].status == "completed"
    assert messages["m-completed-id"].finish_reason == "stop"

    # Streaming message reconciled
    assert messages["m-streaming-id"].status == "interrupted"
    assert messages["m-streaming-id"].finish_reason == "aborted"
    assert "Process terminated unexpectedly" in messages["m-streaming-id"].error_message
    assert messages["m-streaming-id"].content == "Partial text before power outage..."

    # Pending message reconciled
    assert messages["m-pending-id"].status == "interrupted"
    assert messages["m-pending-id"].finish_reason == "aborted"

    # Failed message untouched
    assert messages["m-failed-id"].status == "failed"
    assert messages["m-failed-id"].error_message == "Original error"
