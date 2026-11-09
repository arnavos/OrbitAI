"""Comprehensive integration, adversarial, and reliability test suite for SSE token streaming."""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.backend.conftest import TestingSessionLocal

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
from app.schemas.streaming import STREAM_PROTOCOL_VERSION
from app.services.conversation_service import ConversationService

# ------------------------------------------------------------------------------
# Test Mock Providers
# ------------------------------------------------------------------------------


class DeterministicStreamProvider(ModelProvider):
    """Configurable provider yielding exact token sequences with cooperative cancellation."""

    def __init__(
        self,
        provider_id: str = "mock_det",
        deltas: list[TokenDelta] | None = None,
        delay_per_token: float = 0.005,
    ) -> None:
        self._provider_id = provider_id
        self._deltas = deltas or []
        self._delay = delay_per_token
        self.cancel_event = asyncio.Event()
        self.cancellation_called = False
        self.cleanup_called = False
        self.tokens_yielded = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.MOCK

    @property
    def display_name(self) -> str:
        return f"Deterministic Provider ({self._provider_id})"

    async def check_health(self) -> ProviderHealth:
        return ProviderHealth(provider_id=self._provider_id, status=ProviderStatus.ONLINE)

    async def discover_models(self) -> list[ModelDescriptor]:
        return [
            ModelDescriptor(
                id=f"{self._provider_id}/model",
                name="Deterministic Model",
                provider_type=ProviderType.MOCK,
                capabilities={ModelCapability.STREAMING},
                availability_status=ModelAvailabilityStatus.AVAILABLE,
            )
        ]

    async def cancel_inference(self, request_id: str) -> bool:
        self.cancellation_called = True
        self.cancel_event.set()
        return True

    async def stream_inference(self, request):
        try:
            for d in self._deltas:
                if self.cancel_event.is_set():
                    yield TokenDelta(text="", finish_reason=FinishReason.CANCELLED)
                    return
                if self._delay > 0:
                    await asyncio.sleep(self._delay)
                self.tokens_yielded += 1
                yield d
        finally:
            self.cleanup_called = True


class FailingStreamProvider(ModelProvider):
    """Provider yielding partial tokens then raising an unrecoverable exception."""

    def __init__(
        self,
        provider_id: str = "mock_fail_stream",
        initial_tokens: list[str] | None = None,
        exception_to_raise: Exception | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._initial_tokens = initial_tokens or ["Partial ", "tokens ", "before "]
        self._exception = exception_to_raise or RuntimeError("Inference engine core dump")

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.MOCK

    @property
    def display_name(self) -> str:
        return "Failing Stream Provider"

    async def check_health(self) -> ProviderHealth:
        return ProviderHealth(provider_id=self._provider_id, status=ProviderStatus.ONLINE)

    async def discover_models(self) -> list[ModelDescriptor]:
        return [
            ModelDescriptor(
                id=f"{self._provider_id}/model",
                name="Failing Model",
                provider_type=ProviderType.MOCK,
                capabilities={ModelCapability.STREAMING},
                availability_status=ModelAvailabilityStatus.AVAILABLE,
            )
        ]

    async def stream_inference(self, request):
        for tok in self._initial_tokens:
            await asyncio.sleep(0.005)
            yield TokenDelta(text=tok)
        raise self._exception


class EmptyStreamProvider(ModelProvider):
    """Provider returning an empty iterator without terminal finish reason."""

    @property
    def provider_id(self) -> str:
        return "mock_empty"

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.MOCK

    @property
    def display_name(self) -> str:
        return "Empty Stream Provider"

    async def check_health(self) -> ProviderHealth:
        return ProviderHealth(provider_id="mock_empty", status=ProviderStatus.ONLINE)

    async def discover_models(self) -> list[ModelDescriptor]:
        return [
            ModelDescriptor(
                id="mock_empty/model",
                name="Empty Model",
                provider_type=ProviderType.MOCK,
                capabilities={ModelCapability.STREAMING},
                availability_status=ModelAvailabilityStatus.AVAILABLE,
            )
        ]

    async def stream_inference(self, request):
        if False:
            yield TokenDelta(text="")


# Helper to parse SSE frames from raw string output
def parse_sse_events(raw_sse: str) -> list[dict[str, Any]]:
    import json

    events = []
    blocks = raw_sse.split("\n\n")
    for block in blocks:
        if not block.strip() or block.startswith(":"):
            continue
        lines = block.split("\n")
        ev_type = None
        ev_data = None
        for line in lines:
            if line.startswith("event: "):
                ev_type = line[7:].strip()
            elif line.startswith("data: "):
                ev_data = line[6:].strip()
        if ev_data:
            parsed_json = json.loads(ev_data)
            events.append({"event": ev_type, "data": parsed_json})
    return events


# ------------------------------------------------------------------------------
# Test Cases
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_multi_token_streaming(db_session: AsyncSession):
    """Verify clean multi-token SSE streaming, sequence ordering, and DB finalization."""
    registry = ModelRegistry()
    deltas = [
        TokenDelta(text="The "),
        TokenDelta(text="speed "),
        TokenDelta(text="of "),
        TokenDelta(text="light."),
        TokenDelta(
            text="",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=4, source=UsageSource.REPORTED),
        ),
    ]
    provider = DeterministicStreamProvider(provider_id="norm_p", deltas=deltas)
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Stream Test", model_id="norm_p/model"),
    )

    req = SendMessageRequest(content="What is light?", model_id="norm_p/model")

    raw_output = []
    async for chunk in ConversationService.send_message_stream(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=req,
        registry=registry,
    ):
        raw_output.append(chunk)

    full_sse = "".join(raw_output)
    events = parse_sse_events(full_sse)

    assert len(events) >= 6
    # 1. Started event
    assert events[0]["event"] == "stream.started"
    assert events[0]["data"]["protocol_version"] == STREAM_PROTOCOL_VERSION
    assert events[0]["data"]["payload"]["model_id"] == "norm_p/model"

    # 2. Delta events
    delta_texts = [e["data"]["payload"]["text"] for e in events if e["event"] == "message.delta"]
    assert delta_texts == ["The ", "speed ", "of ", "light."]

    # 3. Completed event
    comp_ev = events[-1]
    assert comp_ev["event"] == "stream.completed"
    assert comp_ev["data"]["payload"]["finish_reason"] == "stop"
    assert comp_ev["data"]["payload"]["prompt_tokens"] == 10
    assert comp_ev["data"]["payload"]["completion_tokens"] == 4

    # 4. Verify DB state
    async with TestingSessionLocal() as session:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.sequence.asc())
        )
        res = await session.execute(stmt)
        messages = list(res.scalars().all())

    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].status == "completed"
    assert messages[1].role == "assistant"
    assert messages[1].status == "completed"
    assert messages[1].content == "The speed of light."
    assert messages[1].finish_reason == "stop"
    assert messages[1].completion_tokens == 4


@pytest.mark.asyncio
async def test_empty_provider_response_fails(db_session: AsyncSession):
    """Verify that an empty provider stream without a finish reason fails deterministically."""
    registry = ModelRegistry()
    await registry.register_provider(EmptyStreamProvider())

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Empty Test", model_id="mock_empty/model"),
    )

    req = SendMessageRequest(content="Empty prompt", model_id="mock_empty/model")

    raw_output = []
    async for chunk in ConversationService.send_message_stream(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=req,
        registry=registry,
    ):
        raw_output.append(chunk)

    events = parse_sse_events("".join(raw_output))
    assert len(events) == 2
    assert events[0]["event"] == "stream.started"
    assert events[1]["event"] == "stream.failed"
    assert "without terminal finish reason" in events[1]["data"]["payload"]["error_message"]

    async with TestingSessionLocal() as session:
        asst = await session.get(Message, events[0]["data"]["message_id"])
        assert asst is not None
        assert asst.status == "failed"
        assert asst.finish_reason == "error"


@pytest.mark.asyncio
async def test_explicit_length_finish_reason(db_session: AsyncSession):
    """Verify LENGTH finish reason is propagated correctly in SSE and DB."""
    registry = ModelRegistry()
    deltas = [
        TokenDelta(text="Bounded "),
        TokenDelta(text="content", finish_reason=FinishReason.LENGTH),
    ]
    await registry.register_provider(
        DeterministicStreamProvider(provider_id="len_p", deltas=deltas)
    )

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Length Test", model_id="len_p/model"),
    )

    req = SendMessageRequest(content="Limit tokens", model_id="len_p/model")

    raw_output = []
    async for chunk in ConversationService.send_message_stream(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=req,
        registry=registry,
    ):
        raw_output.append(chunk)

    events = parse_sse_events("".join(raw_output))
    assert events[-1]["event"] == "stream.completed"
    assert events[-1]["data"]["payload"]["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_provider_exception_persists_partial_and_emits_failed(
    db_session: AsyncSession,
):
    """Verify provider exception persists partial output and emits stream.failed."""
    registry = ModelRegistry()
    await registry.register_provider(FailingStreamProvider(provider_id="fail_p"))

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Fail Test", model_id="fail_p/model"),
    )

    req = SendMessageRequest(content="Fail prompt", model_id="fail_p/model")

    raw_output = []
    async for chunk in ConversationService.send_message_stream(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=req,
        registry=registry,
    ):
        raw_output.append(chunk)

    events = parse_sse_events("".join(raw_output))
    failed_ev = events[-1]
    assert failed_ev["event"] == "stream.failed"
    assert "Inference engine core dump" in failed_ev["data"]["payload"]["error_message"]

    async with TestingSessionLocal() as session:
        asst = await session.get(Message, events[0]["data"]["message_id"])
        assert asst is not None
        assert asst.status == "failed"
        assert asst.finish_reason == "error"
        assert asst.content == "Partial tokens before "  # Partial content preserved!


@pytest.mark.asyncio
async def test_explicit_turn_cancellation(db_session: AsyncSession):
    """Verify cancel_turn marks generation cancelled and persists partial output."""
    registry = ModelRegistry()
    deltas = [TokenDelta(text=f"tok_{i} ") for i in range(20)]
    provider = DeterministicStreamProvider(
        provider_id="slow_cancel_p", deltas=deltas, delay_per_token=0.03
    )
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Cancel Test", model_id="slow_cancel_p/model"),
    )

    req = SendMessageRequest(content="Will cancel", model_id="slow_cancel_p/model")

    stream_task = asyncio.create_task(
        _collect_stream(
            session_factory=TestingSessionLocal,
            conversation_id=conv.id,
            payload=req,
            registry=registry,
        )
    )

    # Allow stream to start and emit some tokens
    await asyncio.sleep(0.08)

    # Execute explicit turn cancellation
    cancelled, msg_id, detail = await ConversationService.cancel_turn(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
    )
    assert cancelled is True
    assert msg_id is not None
    assert provider.cancellation_called is True

    raw_sse = await stream_task
    events = parse_sse_events(raw_sse)

    terminal_ev = events[-1]
    assert terminal_ev["event"] == "stream.cancelled"
    assert terminal_ev["data"]["payload"]["reason"] == "cancelled_by_user"

    async with TestingSessionLocal() as session:
        asst = await session.get(Message, msg_id)
        assert asst is not None
        assert asst.status == "interrupted"
        assert asst.finish_reason == "cancelled"
        assert len(asst.content) > 0  # Partial preserved


@pytest.mark.asyncio
async def test_repeated_cancellation_idempotency(db_session: AsyncSession):
    """Verify repeated cancellation requests on the same conversation are idempotent."""
    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Idempotency Test"),
    )

    # Cancel when no generation is active
    c1, m1, d1 = await ConversationService.cancel_turn(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
    )
    assert c1 is False
    assert m1 is None
    assert "No active generation" in d1

    # Second call returns same result cleanly
    c2, m2, d2 = await ConversationService.cancel_turn(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
    )
    assert c2 is False
    assert m2 is None


@pytest.mark.asyncio
async def test_same_conversation_serialization(db_session: AsyncSession):
    """Verify concurrent requests to the same thread are serialized without data races."""
    registry = ModelRegistry()
    deltas = [
        TokenDelta(text="first "),
        TokenDelta(text="done", finish_reason=FinishReason.STOP),
    ]
    provider = DeterministicStreamProvider(
        provider_id="serial_p", deltas=deltas, delay_per_token=0.02
    )
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Serialization Test", model_id="serial_p/model"),
    )

    req1 = SendMessageRequest(content="Prompt 1", model_id="serial_p/model")
    req2 = SendMessageRequest(content="Prompt 2", model_id="serial_p/model")

    # Launch two streaming requests simultaneously on the SAME thread
    t1 = asyncio.create_task(
        _collect_stream(TestingSessionLocal, conv.id, req1, registry),
    )
    t2 = asyncio.create_task(
        _collect_stream(TestingSessionLocal, conv.id, req2, registry),
    )

    res1, res2 = await asyncio.gather(t1, t2)
    evs1 = parse_sse_events(res1)
    evs2 = parse_sse_events(res2)

    assert evs1[-1]["event"] == "stream.completed"
    assert evs2[-1]["event"] == "stream.completed"

    async with TestingSessionLocal() as session:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.sequence.asc())
        )
        res = await session.execute(stmt)
        msgs = list(res.scalars().all())

    # 4 distinct messages with monotonically increasing sequence numbers: 1, 2, 3, 4!
    assert len(msgs) == 4
    assert [m.sequence for m in msgs] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_conversation_lock_cleanup(db_session: AsyncSession):
    """Verify that conversation in-memory locks are cleaned up after request completes."""
    registry = ModelRegistry()
    provider = DeterministicStreamProvider(
        provider_id="lock_clean_p",
        deltas=[TokenDelta(text="test", finish_reason=FinishReason.STOP)],
    )
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Lock Cleanup Test", model_id="lock_clean_p/model"),
    )

    req = SendMessageRequest(content="Testing lock cleanup", model_id="lock_clean_p/model")
    async for _ in ConversationService.send_message_stream(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=req,
        registry=registry,
    ):
        pass

    # Verify that ref-count cleaned up the lock descriptor completely
    async with ConversationService._locks_guard:
        assert conv.id not in ConversationService._lock_entries


@pytest.mark.asyncio
async def test_rest_streaming_endpoint(async_client: AsyncClient, auth_headers: dict):
    """Test full HTTP REST streaming endpoint over ASGITransport."""
    # Create conversation first
    create_resp = await async_client.post(
        "/api/v1/conversations",
        json={"title": "HTTP Stream Test", "model_id": "ollama/llama3.2:3b"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    conv_id = create_resp.json()["data"]["id"]

    # Register mock in app state
    registry = ModelRegistry()
    provider = DeterministicStreamProvider(
        provider_id="http_stream_p",
        deltas=[
            TokenDelta(text="HTTP "),
            TokenDelta(text="stream ", finish_reason=FinishReason.STOP),
        ],
    )
    await registry.register_provider(provider)

    from app.main import app

    app.state.model_registry = registry

    # Stream from endpoint
    resp = await async_client.post(
        f"/api/v1/conversations/{conv_id}/messages/stream",
        json={"content": "Hello stream!", "model_id": "http_stream_p/model"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert "no-cache" in resp.headers["cache-control"]

    events = parse_sse_events(resp.text)
    assert len(events) >= 3
    assert events[0]["event"] == "stream.started"
    assert events[-1]["event"] == "stream.completed"


@pytest.mark.asyncio
async def test_rest_cancel_endpoint(async_client: AsyncClient, auth_headers: dict):
    """Test explicit cancellation REST endpoint."""
    create_resp = await async_client.post(
        "/api/v1/conversations",
        json={"title": "HTTP Cancel Test"},
        headers=auth_headers,
    )
    conv_id = create_resp.json()["data"]["id"]

    # Cancel endpoint with no active generation
    cancel_resp = await async_client.post(
        f"/api/v1/conversations/{conv_id}/cancel",
        headers=auth_headers,
    )
    assert cancel_resp.status_code == 200
    data = cancel_resp.json()["data"]
    assert data["cancelled"] is False

    # Test 404 for non-existent conversation
    non_existent = "00000000-0000-0000-0000-000000000000"
    missing_resp = await async_client.post(
        f"/api/v1/conversations/{non_existent}/cancel",
        headers=auth_headers,
    )
    assert missing_resp.status_code == 404


@pytest.mark.asyncio
async def test_streaming_endpoint_rejects_empty_content(
    async_client: AsyncClient, auth_headers: dict
):
    """Test that whitespace-only prompts are rejected with 422 before stream begins."""
    create_resp = await async_client.post(
        "/api/v1/conversations",
        json={"title": "Validation Test"},
        headers=auth_headers,
    )
    conv_id = create_resp.json()["data"]["id"]

    resp = await async_client.post(
        f"/api/v1/conversations/{conv_id}/messages/stream",
        json={"content": "   "},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_streaming_endpoint_nonexistent_conv_returns_404(
    async_client: AsyncClient, auth_headers: dict
):
    """Test that a non-existent conversation returns 404 before stream begins."""
    non_existent = "11111111-2222-3333-4444-555555555555"
    resp = await async_client.post(
        f"/api/v1/conversations/{non_existent}/messages/stream",
        json={"content": "Hello"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unauthorized_streaming_rejected(async_client: AsyncClient):
    """Test that missing authorization headers reject streaming."""
    resp = await async_client.post(
        "/api/v1/conversations/123/messages/stream",
        json={"content": "No auth"},
    )
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_large_response_streaming(db_session: AsyncSession):
    """Verify that a large stream with 100+ tokens streams and persists with correct ordering."""
    registry = ModelRegistry()
    token_count = 120
    deltas = [TokenDelta(text=f"w{i} ") for i in range(token_count)]
    deltas.append(
        TokenDelta(
            text="",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(
                prompt_tokens=15,
                completion_tokens=token_count,
                source=UsageSource.REPORTED,
            ),
        )
    )
    provider = DeterministicStreamProvider(
        provider_id="large_p", deltas=deltas, delay_per_token=0.001
    )
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Large Stream", model_id="large_p/model"),
    )

    req = SendMessageRequest(content="Generate large response", model_id="large_p/model")

    raw_chunks = []
    async for chunk in ConversationService.send_message_stream(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=req,
        registry=registry,
    ):
        raw_chunks.append(chunk)

    events = parse_sse_events("".join(raw_chunks))
    # 1 started + 120 deltas + 1 completed = 122 events
    assert len(events) == token_count + 2
    # Verify sequence numbers are strictly monotonically increasing
    seqs = [e["data"]["event_seq"] for e in events]
    assert seqs == list(range(token_count + 2))


@pytest.mark.asyncio
async def test_unexpected_eof_with_partial_tokens_marked_failed(
    db_session: AsyncSession,
):
    """Verify that an unexpected EOF with partial tokens is persisted as failed with partial content."""
    registry = ModelRegistry()
    deltas = [
        TokenDelta(text="Some "),
        TokenDelta(text="words "),
        TokenDelta(text="then silence"),  # No finish_reason
    ]
    provider = DeterministicStreamProvider(provider_id="abrupt_p", deltas=deltas)
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="EOF Test", model_id="abrupt_p/model"),
    )

    req = SendMessageRequest(content="Abrupt stream", model_id="abrupt_p/model")

    raw_output = []
    async for chunk in ConversationService.send_message_stream(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=req,
        registry=registry,
    ):
        raw_output.append(chunk)

    events = parse_sse_events("".join(raw_output))
    assert events[-1]["event"] == "stream.failed"
    assert "without terminal finish reason" in events[-1]["data"]["payload"]["error_message"]

    async with TestingSessionLocal() as session:
        asst = await session.get(Message, events[0]["data"]["message_id"])
        assert asst is not None
        assert asst.status == "failed"
        assert asst.content == "Some words then silence"


@pytest.mark.asyncio
async def test_disconnect_check_interruption(db_session: AsyncSession):
    """Verify disconnect_check callable stops stream, cancels provider, and marks interrupted."""
    registry = ModelRegistry()
    deltas = [TokenDelta(text=f"chunk_{i} ") for i in range(50)]
    provider = DeterministicStreamProvider(
        provider_id="disc_p", deltas=deltas, delay_per_token=0.02
    )
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Disconnect Test", model_id="disc_p/model"),
    )

    req = SendMessageRequest(content="Will disconnect", model_id="disc_p/model")

    # Simulate disconnect after 3 tokens
    count = 0

    async def simulate_disconnect() -> bool:
        nonlocal count
        count += 1
        return count >= 3

    raw_output = []
    async for chunk in ConversationService.send_message_stream(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=req,
        registry=registry,
        disconnect_check=simulate_disconnect,
    ):
        raw_output.append(chunk)

    events = parse_sse_events("".join(raw_output))
    assert events[-1]["event"] in ("stream.interrupted", "stream.cancelled")
    assert provider.cancellation_called is True


@pytest.mark.asyncio
async def test_cancel_during_prefill_before_first_token(db_session: AsyncSession):
    """Verify that cancellation during prefill before first token halts and persists cleanly."""
    registry = ModelRegistry()

    class PrefillHangProvider(ModelProvider):
        def __init__(self):
            self.cancel_event = asyncio.Event()

        @property
        def provider_id(self) -> str:
            return "prefill_hang"

        @property
        def provider_type(self) -> ProviderType:
            return ProviderType.MOCK

        @property
        def display_name(self) -> str:
            return "Prefill Hang"

        async def check_health(self) -> ProviderHealth:
            return ProviderHealth(provider_id="prefill_hang", status=ProviderStatus.ONLINE)

        async def discover_models(self) -> list[ModelDescriptor]:
            return [
                ModelDescriptor(
                    id="prefill_hang/model",
                    name="Hang Model",
                    provider_type=ProviderType.MOCK,
                    capabilities={ModelCapability.STREAMING},
                    availability_status=ModelAvailabilityStatus.AVAILABLE,
                )
            ]

        async def cancel_inference(self, request_id: str) -> bool:
            self.cancel_event.set()
            return True

        async def stream_inference(self, request):
            await self.cancel_event.wait()
            yield TokenDelta(text="", finish_reason=FinishReason.CANCELLED)

    provider = PrefillHangProvider()
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Prefill Cancel", model_id="prefill_hang/model"),
    )

    req = SendMessageRequest(content="Hang prompt", model_id="prefill_hang/model")

    task = asyncio.create_task(
        _collect_stream(TestingSessionLocal, conv.id, req, registry),
    )

    await asyncio.sleep(0.05)
    cancelled, msg_id, detail = await ConversationService.cancel_turn(
        TestingSessionLocal,
        conv.id,
    )
    assert cancelled is True

    raw_output = await task
    events = parse_sse_events(raw_output)
    assert events[-1]["event"] == "stream.cancelled"

    async with TestingSessionLocal() as session:
        asst = await session.get(Message, msg_id)
        assert asst is not None
        assert asst.status == "interrupted"
        assert asst.finish_reason == "cancelled"
        assert asst.content == ""


@pytest.mark.asyncio
async def test_different_conversation_independence(db_session: AsyncSession):
    """Verify that turns on different conversations execute concurrently without blocking."""
    registry = ModelRegistry()
    deltas = [
        TokenDelta(text="fast "),
        TokenDelta(text="finish", finish_reason=FinishReason.STOP),
    ]
    await registry.register_provider(
        DeterministicStreamProvider(provider_id="indep_p", deltas=deltas, delay_per_token=0.01)
    )

    conv1 = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Thread 1", model_id="indep_p/model"),
    )
    conv2 = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Thread 2", model_id="indep_p/model"),
    )

    req1 = SendMessageRequest(content="Msg 1", model_id="indep_p/model")
    req2 = SendMessageRequest(content="Msg 2", model_id="indep_p/model")

    res1, res2 = await asyncio.gather(
        _collect_stream(TestingSessionLocal, conv1.id, req1, registry),
        _collect_stream(TestingSessionLocal, conv2.id, req2, registry),
    )

    assert parse_sse_events(res1)[-1]["event"] == "stream.completed"
    assert parse_sse_events(res2)[-1]["event"] == "stream.completed"


@pytest.mark.asyncio
async def test_crashed_generations_reconciliation(db_session: AsyncSession):
    """Verify that lingering in-flight streaming messages are safely reconciled on startup."""
    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Crash Recovery Thread"),
    )

    # Insert simulated dangling streaming turn
    now = Message(
        id="crashed-asst-msg-123",
        conversation_id=conv.id,
        sequence=1,
        role="assistant",
        content="Generated partial text before host crashed...",
        status="streaming",
    )
    db_session.add(now)
    await db_session.commit()

    # Reconcile
    count = await ConversationService.reconcile_crashed_generations(db_session)
    assert count == 1

    msg = await db_session.get(Message, "crashed-asst-msg-123")
    assert msg is not None
    await db_session.refresh(msg)
    assert msg.status == "interrupted"
    assert msg.finish_reason == "aborted"
    assert "Process terminated unexpectedly" in msg.error_message


# Helper generator collector
async def _collect_stream(session_factory, conversation_id, payload, registry) -> str:
    chunks = []
    async for c in ConversationService.send_message_stream(
        session_factory=session_factory,
        conversation_id=conversation_id,
        payload=payload,
        registry=registry,
    ):
        chunks.append(c)
    return "".join(chunks)
