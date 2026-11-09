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
