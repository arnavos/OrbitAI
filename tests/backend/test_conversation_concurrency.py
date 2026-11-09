"""Tests for concurrency safety, atomic sequence allocation, and thread serialization."""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import ModelProvider
from app.ai.registry import ModelRegistry
from app.ai.types import (
    FinishReason,
    ModelAvailabilityStatus,
    ModelCapability,
    ModelDescriptor,
    ProviderHealth,
    ProviderStatus,
    ProviderType,
    TokenDelta,
    TokenUsage,
    UsageSource,
)
from app.models.conversation import Message
from app.schemas.conversation import ConversationCreate, SendMessageRequest
from app.services.conversation_service import ConversationService


class MockFastProvider(ModelProvider):
    """Mock provider returning immediate token response."""

    @property
    def provider_id(self) -> str:
        return "mock"

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.MOCK

    @property
    def display_name(self) -> str:
        return "Mock Fast Provider"

    async def check_health(self) -> ProviderHealth:
        return ProviderHealth(provider_id="mock", status=ProviderStatus.ONLINE)

    async def discover_models(self) -> list[ModelDescriptor]:
        return [
            ModelDescriptor(
                id="mock/test-model",
                name="Mock Model",
                provider_type=ProviderType.MOCK,
                capabilities={ModelCapability.STREAMING},
                availability_status=ModelAvailabilityStatus.AVAILABLE,
            )
        ]

    async def stream_inference(self, request):
        yield TokenDelta(text="Hello ")
        await asyncio.sleep(0.01)  # Brief yield to test lock concurrency
        yield TokenDelta(
            text="world!",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, source=UsageSource.REPORTED),
        )


@pytest.mark.asyncio
async def test_concurrent_message_submissions_to_same_conversation_are_serialized(
    db_session: AsyncSession,
):
    """Verify 5 concurrent message submissions to the same conversation allocate monotonic, unique sequences."""
    from tests.backend.conftest import TestingSessionLocal

    # 1. Setup registry with mock provider
    registry = ModelRegistry()
    mock_prov = MockFastProvider()
    await registry.register_provider(mock_prov)

    # 2. Create target conversation
    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Concurrency Test Thread", model_id="mock/test-model"),
    )

    # 3. Launch 5 concurrent send_message calls
    N_CALLS = 5

    async def send_worker(idx: int):
        req = SendMessageRequest(
            content=f"Concurrent message {idx}",
            model_id="mock/test-model",
        )
        return await ConversationService.send_message(
            session_factory=TestingSessionLocal,
            conversation_id=conv.id,
            payload=req,
            registry=registry,
        )

    responses = await asyncio.gather(*(send_worker(i) for i in range(N_CALLS)))
    assert len(responses) == N_CALLS

    # 4. Verify in database: exactly 10 messages (5 user + 5 assistant)
    async with TestingSessionLocal() as session:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.sequence.asc())
        )
        res = await session.execute(stmt)
        messages = list(res.scalars().all())

    assert len(messages) == N_CALLS * 2

    # Check sequence numbers: must be strictly monotonic [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    sequences = [m.sequence for m in messages]
    expected_sequences = list(range(1, (N_CALLS * 2) + 1))
    assert sequences == expected_sequences

    # Alternating roles: user, assistant, user, assistant...
    roles = [m.role for m in messages]
    expected_roles = ["user", "assistant"] * N_CALLS
    assert roles == expected_roles

    # Verify conversation last_sequence matches 10
    retrieved_conv = await ConversationService.get_conversation(
        session=db_session, conversation_id=conv.id
    )
    assert retrieved_conv.last_sequence == N_CALLS * 2
    assert retrieved_conv.message_count == N_CALLS * 2


@pytest.mark.asyncio
async def test_concurrent_submissions_to_distinct_conversations_execute_in_parallel(
    db_session: AsyncSession,
):
    """Verify concurrent submissions to distinct conversation threads execute without blocking."""
    from tests.backend.conftest import TestingSessionLocal

    registry = ModelRegistry()
    mock_prov = MockFastProvider()
    await registry.register_provider(mock_prov)

    c1 = await ConversationService.create_conversation(
        session=db_session, payload=ConversationCreate(title="Conv 1", model_id="mock/test-model")
    )
    c2 = await ConversationService.create_conversation(
        session=db_session, payload=ConversationCreate(title="Conv 2", model_id="mock/test-model")
    )

    async def worker(conv_id: str, text: str):
        return await ConversationService.send_message(
            session_factory=TestingSessionLocal,
            conversation_id=conv_id,
            payload=SendMessageRequest(content=text, model_id="mock/test-model"),
            registry=registry,
        )

    r1, r2 = await asyncio.gather(
        worker(c1.id, "Hello in thread 1"),
        worker(c2.id, "Hello in thread 2"),
    )

    assert r1.user_message.sequence == 1
    assert r1.assistant_message.sequence == 2
    assert r2.user_message.sequence == 1
    assert r2.assistant_message.sequence == 2

    # Verify lock registry is empty after completions
    assert len(ConversationService._lock_entries) == 0


@pytest.mark.asyncio
async def test_lock_registry_cleanup_after_completion(db_session: AsyncSession):
    """Verify lock entry is cleanly evicted from registry when request completes."""
    from tests.backend.conftest import TestingSessionLocal

    registry = ModelRegistry()
    await registry.register_provider(MockFastProvider())

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Eviction Test", model_id="mock/test-model"),
    )

    assert len(ConversationService._lock_entries) == 0

    await ConversationService.send_message(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=SendMessageRequest(content="Testing lock eviction", model_id="mock/test-model"),
        registry=registry,
    )

    # Lock entry must have been removed upon completion
    assert len(ConversationService._lock_entries) == 0


@pytest.mark.asyncio
async def test_lock_registry_ref_counts_and_concurrency():
    """Verify waiting requests increment ref_count, share lock, and cleanup when all complete."""
    conv_id = "test-refcount-conv"
    assert conv_id not in ConversationService._lock_entries

    holder_started = asyncio.Event()
    release_holder = asyncio.Event()

    async def holder():
        async with ConversationService._acquire_conversation_lock(conv_id):
            holder_started.set()
            await release_holder.wait()

    async def waiter():
        async with ConversationService._acquire_conversation_lock(conv_id):
            pass

    t_holder = asyncio.create_task(holder())
    await holder_started.wait()

    # Holder is active: ref_count == 1
    assert conv_id in ConversationService._lock_entries
    assert ConversationService._lock_entries[conv_id].ref_count == 1
    initial_lock = ConversationService._lock_entries[conv_id].lock

    # Start waiter
    t_waiter = asyncio.create_task(waiter())
    await asyncio.sleep(0.01)

    # Waiter is queued: ref_count == 2, sharing same lock instance
    assert ConversationService._lock_entries[conv_id].ref_count == 2
    assert ConversationService._lock_entries[conv_id].lock is initial_lock

    # Release holder
    release_holder.set()
    await t_holder
    await t_waiter

    # Registry must be empty after both exit
    assert conv_id not in ConversationService._lock_entries
    assert len(ConversationService._lock_entries) == 0


@pytest.mark.asyncio
async def test_lock_registry_cancellation_while_waiting():
    """Verify cancellation while waiting for a lock decrements ref_count without releasing unowned lock."""
    conv_id = "test-cancel-wait-conv"
    holder_started = asyncio.Event()
    release_holder = asyncio.Event()

    async def holder():
        async with ConversationService._acquire_conversation_lock(conv_id):
            holder_started.set()
            await release_holder.wait()

    async def waiter():
        async with ConversationService._acquire_conversation_lock(conv_id):
            pass

    t_holder = asyncio.create_task(holder())
    await holder_started.wait()

    assert ConversationService._lock_entries[conv_id].ref_count == 1

    t_waiter = asyncio.create_task(waiter())
    await asyncio.sleep(0.01)
    assert ConversationService._lock_entries[conv_id].ref_count == 2

    # Cancel waiter while waiting
    t_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t_waiter

    # Ref count decremented back to 1
    assert ConversationService._lock_entries[conv_id].ref_count == 1

    # Release holder
    release_holder.set()
    await t_holder

    # Entry is cleaned up
    assert conv_id not in ConversationService._lock_entries
    assert len(ConversationService._lock_entries) == 0


@pytest.mark.asyncio
async def test_lock_registry_cancellation_while_holding():
    """Verify cancellation while holding lock releases lock and decrements ref_count to zero."""
    conv_id = "test-cancel-hold-conv"
    holder_started = asyncio.Event()

    async def holder():
        async with ConversationService._acquire_conversation_lock(conv_id):
            holder_started.set()
            await asyncio.sleep(10.0)

    t_holder = asyncio.create_task(holder())
    await holder_started.wait()

    assert ConversationService._lock_entries[conv_id].ref_count == 1
    assert ConversationService._lock_entries[conv_id].lock.locked()

    t_holder.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t_holder

    # Lock must be released and entry removed
    assert conv_id not in ConversationService._lock_entries
    assert len(ConversationService._lock_entries) == 0


@pytest.mark.asyncio
async def test_lock_registry_thousands_of_conversations_bounded_memory():
    """Verify repeatedly accessing thousands of distinct conversation IDs leaves zero leaked entries."""
    for i in range(1000):
        c_id = f"stress-conv-{i}"
        async with ConversationService._acquire_conversation_lock(c_id):
            pass

    assert len(ConversationService._lock_entries) == 0
