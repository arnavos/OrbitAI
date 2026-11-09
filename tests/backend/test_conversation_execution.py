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
    FinishReason,
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


class MockCustomStreamProvider(ModelProvider):
    """Mock provider allowing programmatic injection of delta streams."""

    def __init__(
        self,
        provider_id: str,
        deltas: list[TokenDelta] | None = None,
        raise_exc: Exception | None = None,
    ):
        self._provider_id = provider_id
        self._deltas = deltas or []
        self._raise_exc = raise_exc
        self.cleanup_called = False

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.MOCK

    @property
    def display_name(self) -> str:
        return f"Mock Provider {self._provider_id}"

    async def check_health(self) -> ProviderHealth:
        return ProviderHealth(provider_id=self._provider_id, status=ProviderStatus.ONLINE)

    async def discover_models(self) -> list[ModelDescriptor]:
        return [
            ModelDescriptor(
                id=f"{self._provider_id}/model",
                name=f"{self._provider_id} Model",
                provider_type=ProviderType.MOCK,
                capabilities={ModelCapability.STREAMING},
                availability_status=ModelAvailabilityStatus.AVAILABLE,
            )
        ]

    async def stream_inference(self, request):
        try:
            for d in self._deltas:
                yield d
            if self._raise_exc:
                raise self._raise_exc
        finally:
            self.cleanup_called = True


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


@pytest.mark.asyncio
async def test_stream_terminating_without_finish_reason_marked_failed(db_session: AsyncSession):
    """Verify unterminated stream (tokens without finish_reason) is marked failed in DB."""
    from tests.backend.conftest import TestingSessionLocal

    provider = MockCustomStreamProvider(
        provider_id="mock_unterminated",
        deltas=[TokenDelta(text="Partial text before abrupt cutoff")],
    )
    registry = ModelRegistry()
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Unterminated Test", model_id="mock_unterminated/model"),
    )

    req = SendMessageRequest(content="Prompt to unterminated provider")
    with pytest.raises(AIError) as exc_info:
        await ConversationService.send_message(
            session_factory=TestingSessionLocal,
            conversation_id=conv.id,
            payload=req,
            registry=registry,
        )

    assert exc_info.value.code == "INFERENCE_FAILED"

    # Verify database state: user message completed, assistant message marked failed with error reason
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
    assert asst_m.status == "failed"
    assert asst_m.finish_reason == "error"
    assert asst_m.content == "Partial text before abrupt cutoff"
    assert "without terminal finish reason" in (asst_m.error_message or "")


@pytest.mark.asyncio
async def test_empty_stream_marked_failed(db_session: AsyncSession):
    """Verify empty stream (zero tokens, no finish_reason) is marked failed in DB."""
    from tests.backend.conftest import TestingSessionLocal

    provider = MockCustomStreamProvider(provider_id="mock_empty", deltas=[])
    registry = ModelRegistry()
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Empty Stream Test", model_id="mock_empty/model"),
    )

    req = SendMessageRequest(content="Prompt to empty stream")
    with pytest.raises(AIError) as exc_info:
        await ConversationService.send_message(
            session_factory=TestingSessionLocal,
            conversation_id=conv.id,
            payload=req,
            registry=registry,
        )

    assert exc_info.value.code == "INFERENCE_FAILED"

    async with TestingSessionLocal() as session:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.sequence.asc())
        )
        res = await session.execute(stmt)
        messages = list(res.scalars().all())

    assert len(messages) == 2
    assert messages[0].status == "completed"
    assert messages[1].status == "failed"
    assert messages[1].finish_reason == "error"


@pytest.mark.asyncio
async def test_stream_explicit_stop_finish_reason(db_session: AsyncSession):
    """Verify stream ending with explicit STOP finish_reason is marked completed."""
    from tests.backend.conftest import TestingSessionLocal

    provider = MockCustomStreamProvider(
        provider_id="mock_stop",
        deltas=[
            TokenDelta(text="Complete and verified output text."),
            TokenDelta(finish_reason=FinishReason.STOP),
        ],
    )
    registry = ModelRegistry()
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Stop Test", model_id="mock_stop/model"),
    )

    req = SendMessageRequest(content="Prompt to stop provider")
    resp = await ConversationService.send_message(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=req,
        registry=registry,
    )

    assert resp.assistant_message.status == "completed"
    assert resp.assistant_message.finish_reason == "stop"
    assert resp.assistant_message.content == "Complete and verified output text."

    async with TestingSessionLocal() as session:
        asst = await session.get(Message, resp.assistant_message.id)
        assert asst is not None
        assert asst.status == "completed"
        assert asst.finish_reason == "stop"


@pytest.mark.asyncio
async def test_stream_explicit_length_finish_reason(db_session: AsyncSession):
    """Verify stream ending with explicit LENGTH finish_reason is marked completed."""
    from tests.backend.conftest import TestingSessionLocal

    provider = MockCustomStreamProvider(
        provider_id="mock_len",
        deltas=[
            TokenDelta(text="Truncated output at token budget limit..."),
            TokenDelta(finish_reason=FinishReason.LENGTH),
        ],
    )
    registry = ModelRegistry()
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Length Test", model_id="mock_len/model"),
    )

    req = SendMessageRequest(content="Prompt to length provider")
    resp = await ConversationService.send_message(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=req,
        registry=registry,
    )

    assert resp.assistant_message.status == "completed"
    assert resp.assistant_message.finish_reason == "length"

    async with TestingSessionLocal() as session:
        asst = await session.get(Message, resp.assistant_message.id)
        assert asst is not None
        assert asst.status == "completed"
        assert asst.finish_reason == "length"


@pytest.mark.asyncio
async def test_stream_terminal_event_followed_by_cleanup(db_session: AsyncSession):
    """Verify stream cleanup executes and terminal event is recorded."""
    from tests.backend.conftest import TestingSessionLocal

    provider = MockCustomStreamProvider(
        provider_id="mock_cleanup",
        deltas=[
            TokenDelta(text="Text before cleanup"),
            TokenDelta(finish_reason=FinishReason.STOP),
        ],
    )
    registry = ModelRegistry()
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Cleanup Test", model_id="mock_cleanup/model"),
    )

    req = SendMessageRequest(content="Prompt for cleanup check")
    await ConversationService.send_message(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=req,
        registry=registry,
    )

    assert provider.cleanup_called is True
