"""Regression tests verifying canonical ORBIT assistant identity and persona enforcement."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.backend.conftest import TestingSessionLocal

from app.ai.context import ContextManager
from app.ai.prompts import ORBIT_SYSTEM_PROMPT, resolve_system_prompt
from app.ai.provider import ModelProvider
from app.ai.registry import ModelRegistry
from app.ai.types import (
    FinishReason,
    InferenceRequest,
    MessageRole,
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
from app.core.config import settings
from app.models.conversation import Message
from app.schemas.conversation import ConversationCreate, SendMessageRequest
from app.services.conversation_service import ConversationService


class RequestCaptureProvider(ModelProvider):
    """Provider capturing the exact InferenceRequest dispatched by ConversationService."""

    def __init__(self, provider_id: str = "capture_p") -> None:
        self._provider_id = provider_id
        self.captured_request: InferenceRequest | None = None

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.MOCK

    @property
    def display_name(self) -> str:
        return f"Capture Provider ({self._provider_id})"

    async def check_health(self) -> ProviderHealth:
        return ProviderHealth(provider_id=self._provider_id, status=ProviderStatus.ONLINE)

    async def discover_models(self) -> list[ModelDescriptor]:
        return [
            ModelDescriptor(
                id=f"{self._provider_id}/model",
                name="Capture Model",
                provider_type=ProviderType.MOCK,
                capabilities={ModelCapability.STREAMING},
                availability_status=ModelAvailabilityStatus.AVAILABLE,
            )
        ]

    async def stream_inference(self, request: InferenceRequest):
        self.captured_request = request
        yield TokenDelta(text="Hello from ORBIT.")
        yield TokenDelta(
            text="",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=4, source=UsageSource.REPORTED),
        )


def _make_msg(seq: int, role: str, content: str, status: str = "completed") -> Message:
    return Message(
        id=f"msg-{seq}",
        conversation_id="conv-test",
        sequence=seq,
        role=role,
        content=content,
        status=status,
    )


def test_canonical_system_prompt_identifies_orbit():
    """Requirement 1: The canonical system prompt identifies ORBIT and persona attributes."""
    assert "You are ORBIT" in ORBIT_SYSTEM_PROMPT
    assert "Autonomous Personal Intelligence Runtime" in ORBIT_SYSTEM_PROMPT
    assert "macOS" in ORBIT_SYSTEM_PROMPT
    assert "local-first" in ORBIT_SYSTEM_PROMPT

    # Verify settings and ContextManager expose the exact canonical prompt
    assert settings.default_system_prompt == ORBIT_SYSTEM_PROMPT
    assert ContextManager.DEFAULT_SYSTEM_PROMPT == ORBIT_SYSTEM_PROMPT


def test_no_default_prompt_contains_orbital():
    """Requirement 2: No default prompt claims or adopts the identity 'Orbital'."""
    assert "I'm Orbital" not in ORBIT_SYSTEM_PROMPT
    assert "I am Orbital" not in ORBIT_SYSTEM_PROMPT
    assert "friendly AI assistant" not in ORBIT_SYSTEM_PROMPT

    # The canonical prompt explicitly forbids the model from calling itself Orbital
    assert "Never refer to yourself as Orbital" in ORBIT_SYSTEM_PROMPT

    # Fallbacks and default resolutions also never produce Orbital
    resolved_none = resolve_system_prompt(None)
    assert "You are ORBIT" in resolved_none
    assert "Never refer to yourself as Orbital" in resolved_none

    resolved_empty = resolve_system_prompt("   ")
    assert "You are ORBIT" in resolved_empty


def test_system_instructions_preserved_during_context_pruning():
    """Requirement 3: System instructions are anchored at index 0 and preserved during pruning."""
    allocated_context = 512

    # Generate 12 historical turns that will exceed remaining context budget
    history = []
    for i in range(1, 13):
        role = "user" if i % 2 == 1 else "assistant"
        history.append(_make_msg(i, role, f"Historical message turn {i} with descriptive text."))

    latest_user = "What is your identity?"

    assembled, max_out, est_tokens = ContextManager.assemble_context(
        history=history,
        latest_user_content=latest_user,
        allocated_context=allocated_context,
        system_prompt=ORBIT_SYSTEM_PROMPT,
    )

    # 1. System prompt MUST be anchored at index 0
    assert len(assembled) >= 3
    assert assembled[0].role == MessageRole.SYSTEM
    assert assembled[0].content == ORBIT_SYSTEM_PROMPT

    # 2. Latest user message MUST be at the end
    assert assembled[-1].role == MessageRole.USER
    assert assembled[-1].content == latest_user

    # 3. Oldest turns must have been pruned while system instruction remained anchored
    middle_msgs = assembled[1:-1]
    # Sequence 1 and 2 should have been pruned to fit budget
    middle_contents = [m.content for m in middle_msgs]
    assert "Historical message turn 1 with descriptive text." not in middle_contents


@pytest.mark.asyncio
async def test_system_instructions_reach_provider_request_on_streaming(db_session: AsyncSession):
    """Requirement 4: System instructions reach the actual provider request during streaming."""
    registry = ModelRegistry()
    provider = RequestCaptureProvider(provider_id="stream_cap")
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Identity Stream Test", model_id="stream_cap/model"),
    )

    req = SendMessageRequest(
        content="What is your name, and what does your name stand for?",
        model_id="stream_cap/model",
        system_prompt=None,  # No custom prompt passed; default canonical must apply
    )

    raw_output = []
    async for chunk in ConversationService.send_message_stream(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=req,
        registry=registry,
    ):
        raw_output.append(chunk)

    assert provider.captured_request is not None
    messages = provider.captured_request.messages

    # First message in the request sent to provider must be the canonical system message
    assert len(messages) >= 2
    assert messages[0].role == MessageRole.SYSTEM
    assert messages[0].content == ORBIT_SYSTEM_PROMPT
    assert messages[-1].role == MessageRole.USER
    assert messages[-1].content == "What is your name, and what does your name stand for?"


@pytest.mark.asyncio
async def test_system_instructions_reach_provider_request_on_sync_submission(
    db_session: AsyncSession,
):
    """Requirement 4b: System instructions reach the actual provider request during sync submission."""
    registry = ModelRegistry()
    provider = RequestCaptureProvider(provider_id="sync_cap")
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="Identity Sync Test", model_id="sync_cap/model"),
    )

    req = SendMessageRequest(
        content="Identify yourself.",
        model_id="sync_cap/model",
        system_prompt=None,
    )

    await ConversationService.send_message(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=req,
        registry=registry,
    )

    assert provider.captured_request is not None
    messages = provider.captured_request.messages

    assert messages[0].role == MessageRole.SYSTEM
    assert messages[0].content == ORBIT_SYSTEM_PROMPT
    assert messages[-1].role == MessageRole.USER
    assert messages[-1].content == "Identify yourself."


def test_custom_system_prompt_augments_canonical_identity():
    """Verify custom user instructions preserve the canonical ORBIT identity."""
    custom = "You are helping the user write Python unit tests."
    resolved = resolve_system_prompt(custom)

    # Core ORBIT identity is preserved
    assert "You are ORBIT" in resolved
    assert "Never refer to yourself as Orbital" in resolved

    # Custom instruction is appended
    assert "Additional instructions:" in resolved
    assert "You are helping the user write Python unit tests." in resolved


@pytest.mark.asyncio
async def test_existing_conversation_context_reconstruction_retains_system_prompt(
    db_session: AsyncSession,
):
    """Verify system prompt is consistently applied to existing conversations with history."""
    registry = ModelRegistry()
    provider = RequestCaptureProvider(provider_id="hist_cap")
    await registry.register_provider(provider)

    conv = await ConversationService.create_conversation(
        session=db_session,
        payload=ConversationCreate(title="History Test", model_id="hist_cap/model"),
    )

    # Turn 1
    req1 = SendMessageRequest(content="First message", model_id="hist_cap/model")
    await ConversationService.send_message(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=req1,
        registry=registry,
    )

    # Turn 2 (simulating a follow-up turn or new session after restart)
    req2 = SendMessageRequest(content="Second message", model_id="hist_cap/model")
    await ConversationService.send_message(
        session_factory=TestingSessionLocal,
        conversation_id=conv.id,
        payload=req2,
        registry=registry,
    )

    assert provider.captured_request is not None
    messages = provider.captured_request.messages

    # Sequence must be: [system, user(1), assistant(1), user(2)]
    assert len(messages) == 4
    assert messages[0].role == MessageRole.SYSTEM
    assert messages[0].content == ORBIT_SYSTEM_PROMPT
    assert messages[1].role == MessageRole.USER
    assert messages[1].content == "First message"
    assert messages[2].role == MessageRole.ASSISTANT
    assert messages[3].role == MessageRole.USER
    assert messages[3].content == "Second message"
