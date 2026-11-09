"""Tests for conversation and message persistence, CRUD, sequencing, and cascade deletion."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Message
from app.schemas.conversation import ConversationCreate, ConversationUpdate
from app.services.conversation_service import ConversationService


@pytest.mark.asyncio
async def test_conversation_create_and_retrieve(db_session: AsyncSession):
    """Verify creating a conversation and retrieving it with message count."""
    payload = ConversationCreate(title="Test Architecture Chat", model_id="ollama/llama3.2:3b")
    conv = await ConversationService.create_conversation(session=db_session, payload=payload)

    assert conv.id is not None
    assert conv.title == "Test Architecture Chat"
    assert conv.model_id == "ollama/llama3.2:3b"
    assert conv.last_sequence == 0
    assert conv.message_count == 0

    retrieved = await ConversationService.get_conversation(
        session=db_session, conversation_id=conv.id
    )
    assert retrieved.id == conv.id
    assert retrieved.title == conv.title


@pytest.mark.asyncio
async def test_conversation_list_and_update(db_session: AsyncSession):
    """Verify listing conversations with ordering and updating title/model."""
    c1 = await ConversationService.create_conversation(
        session=db_session, payload=ConversationCreate(title="Thread 1")
    )
    c2 = await ConversationService.create_conversation(
        session=db_session, payload=ConversationCreate(title="Thread 2")
    )

    items, total = await ConversationService.list_conversations(session=db_session, limit=10)
    assert total >= 2
    ids = [c.id for c in items]
    assert c1.id in ids
    assert c2.id in ids

    # Update title
    updated = await ConversationService.update_conversation(
        session=db_session,
        conversation_id=c1.id,
        payload=ConversationUpdate(title="Renamed Thread 1", model_id="ollama/qwen3:4b"),
    )
    assert updated.title == "Renamed Thread 1"
    assert updated.model_id == "ollama/qwen3:4b"


@pytest.mark.asyncio
async def test_cascade_delete_removes_messages(db_session: AsyncSession):
    """Verify deleting a conversation cascades to delete all contained messages."""
    conv = await ConversationService.create_conversation(
        session=db_session, payload=ConversationCreate(title="To Delete")
    )

    # Insert messages into this conversation
    m1 = Message(
        id="m1-test-id",
        conversation_id=conv.id,
        sequence=1,
        role="user",
        content="Hello world",
        status="completed",
    )
    m2 = Message(
        id="m2-test-id",
        conversation_id=conv.id,
        sequence=2,
        role="assistant",
        content="Hi there",
        status="completed",
    )
    db_session.add_all([m1, m2])
    await db_session.commit()

    # Verify messages exist
    res = await db_session.execute(select(Message).where(Message.conversation_id == conv.id))
    assert len(res.scalars().all()) == 2

    # Delete conversation
    deleted = await ConversationService.delete_conversation(
        session=db_session, conversation_id=conv.id
    )
    assert deleted is True

    # Verify messages were cascaded
    res_after = await db_session.execute(select(Message).where(Message.conversation_id == conv.id))
    assert len(res_after.scalars().all()) == 0


@pytest.mark.asyncio
async def test_unique_sequence_constraint_enforced(db_session: AsyncSession):
    """Verify compound unique constraint on (conversation_id, sequence) prevents duplicate turns."""
    conv = await ConversationService.create_conversation(
        session=db_session, payload=ConversationCreate(title="Sequence Test")
    )

    m1 = Message(
        id="m1-unique-id",
        conversation_id=conv.id,
        sequence=1,
        role="user",
        content="Turn 1",
        status="completed",
    )
    db_session.add(m1)
    await db_session.commit()

    # Attempt to insert another message with duplicate sequence 1
    m2 = Message(
        id="m2-unique-id",
        conversation_id=conv.id,
        sequence=1,  # Duplicate sequence!
        role="assistant",
        content="Duplicate turn",
        status="completed",
    )
    db_session.add(m2)

    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()
