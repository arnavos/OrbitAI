"""Tests for synchronous inference execution, failure handling, cancellation, and REST API."""

import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.exceptions import AIError
from app.ai.provider import ModelProvider
from app.ai.registry import ModelRegistry
from app.ai.types import (
    ModelAvailabilityStatus,
    ModelCapability,
    ModelDescriptor,
    ProviderHealth,
    ProviderStatus,
    ProviderType,
    TokenDelta,
)
from app.models.conversation import Message
from app.schemas.conversation import ConversationCreate, SendMessageRequest
from app.services.conversation_service import ConversationService


class MockFailingProvider(ModelProvider):
    """Mock provider simulating provider failures during streaming."""

    @property
    def provider_id(self) -> str:
        return "mock_fail"

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.MOCK

    @property
    def display_name(self) -> str:
        return "Mock Failing Provider"

    async def check_health(self) -> ProviderHealth:
        return ProviderHealth(provider_id="mock_fail", status=ProviderStatus.ONLINE)

    async def discover_models(self) -> list[ModelDescriptor]:
        return [
            ModelDescriptor(
                id="mock_fail/fail-model",
                name="Failing Model",
                provider_type=ProviderType.MOCK,
                capabilities={ModelCapability.STREAMING},
                availability_status=ModelAvailabilityStatus.AVAILABLE,
            )
        ]

    async def stream_inference(self, request):
        yield TokenDelta(text="Initial tokens before crash...")
        await asyncio.sleep(0.01)
        raise RuntimeError("GPU Driver crashed unexpectedly!")


class MockSlowCancellableProvider(ModelProvider):
    """Mock provider that yields tokens slowly so cancellation can be injected."""

    @property
    def provider_id(self) -> str:
        return "mock_slow"

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.MOCK

    @property
    def display_name(self) -> str:
        return "Mock Slow Cancellable Provider"

    async def check_health(self) -> ProviderHealth:
        return ProviderHealth(provider_id="mock_slow", status=ProviderStatus.ONLINE)

    async def discover_models(self) -> list[ModelDescriptor]:
        return [
            ModelDescriptor(
                id="mock_slow/slow-model",
                name="Slow Model",
                provider_type=ProviderType.MOCK,
                capabilities={ModelCapability.STREAMING},
                availability_status=ModelAvailabilityStatus.AVAILABLE,
            )
        ]

    async def stream_inference(self, request):
        yield TokenDelta(text="First partial chunk. ")
        try:
            await asyncio.sleep(5.0)  # Long sleep to await cancellation
            yield TokenDelta(text="Should not be reached")
        except asyncio.CancelledError:
            # Propagate cancellation
            raise


@pytest.mark.asyncio
async def test_user_message_persisted_when_provider_fails(db_session: AsyncSession):
    """Verify user message remains safely committed even when model provider throws an error."""
    from tests.backend.conftest import TestingSessionLocal

    registry = ModelRegistry()
    await registry.register_provider(MockFailingProvider())

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Failure Test", model_id="mock_fail/fail-model"),
    )

    req = SendMessageRequest(
        content="Important user question that must not be lost",
        model_id="mock_fail/fail-model",
    )

    # Provider will fail with AIError during inference
    with pytest.raises(AIError, match="Model inference failed"):
        await ConversationService.send_message(
            session_factory=TestingSessionLocal,
            conversation_id=conv.id,
            payload=req,
            registry=registry,
        )

    # Verify database state
    async with TestingSessionLocal() as session:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.sequence.asc())
        )
        res = await session.execute(stmt)
        messages = list(res.scalars().all())

    # User message (seq 1) and Assistant message (seq 2) must both exist!
    assert len(messages) == 2

    user_m = messages[0]
    assert user_m.role == "user"
    assert user_m.content == "Important user question that must not be lost"
    assert user_m.status == "completed"

    asst_m = messages[1]
    assert asst_m.role == "assistant"
    assert asst_m.status == "failed"
    assert asst_m.finish_reason == "error"
    assert "GPU Driver crashed unexpectedly" in asst_m.error_message
    assert "Initial tokens" in asst_m.content  # Partial output preserved!


@pytest.mark.asyncio
async def test_async_cancellation_marks_interrupted_and_reraises(db_session: AsyncSession):
    """Verify asyncio.CancelledError marks assistant as interrupted, preserves partial tokens, and re-raises."""
    from tests.backend.conftest import TestingSessionLocal

    registry = ModelRegistry()
    await registry.register_provider(MockSlowCancellableProvider())

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Cancellation Test", model_id="mock_slow/slow-model"),
    )

    req = SendMessageRequest(
        content="Prompt that will be cancelled",
        model_id="mock_slow/slow-model",
    )

    task = asyncio.create_task(
        ConversationService.send_message(
            session_factory=TestingSessionLocal,
            conversation_id=conv.id,
            payload=req,
            registry=registry,
        )
    )

    # Allow task to start streaming and emit initial chunk
    await asyncio.sleep(0.05)
    # Cancel task
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Check database state: user turn completed, assistant turn interrupted
    async with TestingSessionLocal() as session:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.sequence.asc())
        )
        res = await session.execute(stmt)
        messages = list(res.scalars().all())

    assert len(messages) == 2
    user_m, asst_m = messages[0], messages[1]

    assert user_m.status == "completed"
    assert asst_m.status == "interrupted"
    assert asst_m.finish_reason == "cancelled"
    assert "First partial chunk" in asst_m.content


@pytest.mark.asyncio
async def test_conversation_rest_endpoints_crud(async_client: AsyncClient, auth_headers: dict):
    """Verify REST API CRUD endpoints for /api/v1/conversations."""
    # 1. Create conversation
    create_resp = await async_client.post(
        "/api/v1/conversations",
        json={"title": "REST API Test Conversation"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    create_data = create_resp.json()["data"]
    conv_id = create_data["id"]
    assert create_data["title"] == "REST API Test Conversation"

    # 2. List conversations
    list_resp = await async_client.get("/api/v1/conversations", headers=auth_headers)
    assert list_resp.status_code == 200
    list_data = list_resp.json()["data"]
    assert list_data["total"] >= 1
    assert any(c["id"] == conv_id for c in list_data["conversations"])

    # 3. Get conversation
    get_resp = await async_client.get(f"/api/v1/conversations/{conv_id}", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["id"] == conv_id

    # 4. Patch conversation
    patch_resp = await async_client.patch(
        f"/api/v1/conversations/{conv_id}",
        json={"title": "Updated REST Title"},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["data"]["title"] == "Updated REST Title"

    # 5. List messages (empty initially)
    msg_list_resp = await async_client.get(
        f"/api/v1/conversations/{conv_id}/messages", headers=auth_headers
    )
    assert msg_list_resp.status_code == 200
    assert msg_list_resp.json()["data"]["total"] == 0

    # 6. Delete conversation
    del_resp = await async_client.delete(f"/api/v1/conversations/{conv_id}", headers=auth_headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["data"] is True

    # 7. Get after delete returns 404
    get_after = await async_client.get(f"/api/v1/conversations/{conv_id}", headers=auth_headers)
    assert get_after.status_code == 404
