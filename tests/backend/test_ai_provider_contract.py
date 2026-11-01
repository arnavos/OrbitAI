"""Unit tests verifying the ModelProvider abstract contract and execution semantics."""

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.ai.exceptions import (
    ModelNotFoundError,
    ProviderUnavailableError,
    UnsupportedCapabilityError,
)
from app.ai.provider import ModelProvider
from app.ai.types import (
    ChatMessage,
    FinishReason,
    InferenceRequest,
    MessageRole,
    ModelCapability,
    ModelDescriptor,
    ProviderHealth,
    ProviderStatus,
    ProviderType,
    TokenDelta,
    TokenUsage,
    UsageSource,
)


class MockTestProvider(ModelProvider):
    """Deterministic mock provider implementing ModelProvider for testing purposes only."""

    def __init__(
        self,
        provider_id: str = "mock-provider",
        supported_capabilities: set[ModelCapability] | None = None,
        should_fail_health: bool = False,
        should_fail_discovery: bool = False,
    ):
        self._provider_id = provider_id
        self._capabilities = supported_capabilities or {ModelCapability.STREAMING}
        self.should_fail_health = should_fail_health
        self.should_fail_discovery = should_fail_discovery
        self.unloaded_models: list[str] = []
        self.cancelled_requests: list[str] = []

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def provider_type(self) -> ProviderType | str:
        return ProviderType.MOCK

    @property
    def display_name(self) -> str:
        return "Deterministic Mock Provider"

    def get_supported_capabilities(self) -> set[ModelCapability]:
        return self._capabilities

    async def check_health(self) -> ProviderHealth:
        if self.should_fail_health:
            return ProviderHealth(
                provider_id=self.provider_id,
                status=ProviderStatus.OFFLINE,
                message="Mock daemon simulated offline state",
            )
        return ProviderHealth(
            provider_id=self.provider_id,
            status=ProviderStatus.ONLINE,
            latency_ms=0.5,
            message="Mock operational",
        )

    async def discover_models(self) -> list[ModelDescriptor]:
        if self.should_fail_discovery:
            raise ProviderUnavailableError("Simulated provider discovery connection refused")

        return [
            ModelDescriptor(
                id=f"{self.provider_id}/mock-model-1",
                name="Mock Model 1",
                provider_type=self.provider_type,
                context_window_capacity=4096,
                capabilities={ModelCapability.STREAMING},
            ),
            ModelDescriptor(
                id=f"{self.provider_id}/mock-model-2",
                name="Mock Model 2",
                provider_type=self.provider_type,
                context_window_capacity=8192,
                capabilities={ModelCapability.STREAMING, ModelCapability.CANCELLATION},
            ),
        ]

    async def stream_inference(self, request: InferenceRequest) -> AsyncIterator[TokenDelta]:
        valid_ids = [f"{self.provider_id}/mock-model-1", f"{self.provider_id}/mock-model-2"]
        if request.model_id not in valid_ids:
            raise ModelNotFoundError(f"Model '{request.model_id}' not found in mock provider")

        tokens = ["Hello", " world", " from", " ORBIT", "!"]
        for token in tokens:
            await asyncio.sleep(0.001)
            yield TokenDelta(text=token)

        yield TokenDelta(
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(
                prompt_tokens=4,
                completion_tokens=5,
                source=UsageSource.REPORTED,
            ),
        )

    async def unload_model(self, model_id: str) -> bool:
        if not self.supports_unloading():
            return await super().unload_model(model_id)
        self.unloaded_models.append(model_id)
        return True

    async def cancel_inference(self, request_id: str) -> bool:
        if not self.supports_cancellation():
            return await super().cancel_inference(request_id)
        self.cancelled_requests.append(request_id)
        return True


# ==============================================================================
# Contract Conformance Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_provider_health_check():
    """Verify provider health probe reports correct status."""
    online_provider = MockTestProvider(should_fail_health=False)
    health = await online_provider.check_health()
    assert health.status == ProviderStatus.ONLINE
    assert health.provider_id == "mock-provider"
    assert health.latency_ms is not None

    offline_provider = MockTestProvider(should_fail_health=True)
    offline_health = await offline_provider.check_health()
    assert offline_health.status == ProviderStatus.OFFLINE


@pytest.mark.asyncio
async def test_provider_model_discovery():
    """Verify model discovery returns valid descriptors."""
    provider = MockTestProvider(provider_id="test-p1")
    models = await provider.discover_models()
    assert len(models) == 2
    assert models[0].id == "test-p1/mock-model-1"
    assert models[0].context_window_capacity == 4096


@pytest.mark.asyncio
async def test_provider_model_discovery_failure_propagation():
    """Verify discovery exceptions propagate correctly."""
    failing_provider = MockTestProvider(should_fail_discovery=True)
    with pytest.raises(ProviderUnavailableError, match="connection refused"):
        await failing_provider.discover_models()


@pytest.mark.asyncio
async def test_provider_streaming_inference():
    """Verify async streaming yields incremental tokens and completion chunk."""
    provider = MockTestProvider()
    req = InferenceRequest(
        model_id="mock-provider/mock-model-1",
        messages=[ChatMessage(role=MessageRole.USER, content="Test prompt")],
    )

    collected_text = []
    finish_reason = None
    final_usage = None

    async for chunk in provider.stream_inference(req):
        if chunk.text:
            collected_text.append(chunk.text)
        if chunk.finish_reason is not None:
            finish_reason = chunk.finish_reason
            final_usage = chunk.usage

    assert "".join(collected_text) == "Hello world from ORBIT!"
    assert finish_reason == FinishReason.STOP
    assert final_usage is not None
    assert final_usage.total_tokens == 9


@pytest.mark.asyncio
async def test_provider_streaming_unknown_model_raises_not_found():
    """Verify streaming with unregistered model raises ModelNotFoundError."""
    provider = MockTestProvider()
    req = InferenceRequest(
        model_id="mock-provider/nonexistent-model",
        messages=[ChatMessage(role=MessageRole.USER, content="Hello")],
    )

    with pytest.raises(ModelNotFoundError, match="not found in mock provider"):
        async for _ in provider.stream_inference(req):
            pass


@pytest.mark.asyncio
async def test_unload_model_unsupported_raises_unsupported_capability_error():
    """Verify calling unload_model on a provider without MODEL_UNLOADING raises UnsupportedCapabilityError."""
    provider = MockTestProvider(supported_capabilities={ModelCapability.STREAMING})
    assert provider.supports_unloading() is False

    with pytest.raises(
        UnsupportedCapabilityError, match="does not support explicit model unloading"
    ):
        await provider.unload_model("mock-provider/mock-model-1")


@pytest.mark.asyncio
async def test_unload_model_advertised_unimplemented_raises_not_implemented_error():
    """Verify calling unload_model on provider advertising but omitting implementation raises NotImplementedError."""
    provider = IncompleteCapabilityProvider()
    assert provider.supports_unloading() is True

    with pytest.raises(
        NotImplementedError,
        match="advertises ModelCapability.MODEL_UNLOADING but did not implement unload_model",
    ):
        await provider.unload_model("any-model")


@pytest.mark.asyncio
async def test_unload_model_overridden_success():
    """Verify calling unload_model succeeds when provider explicitly implements it."""
    provider = MockTestProvider(
        supported_capabilities={ModelCapability.STREAMING, ModelCapability.MODEL_UNLOADING}
    )
    assert provider.supports_unloading() is True

    result = await provider.unload_model("mock-provider/mock-model-1")
    assert result is True
    assert "mock-provider/mock-model-1" in provider.unloaded_models


@pytest.mark.asyncio
async def test_cancel_inference_unsupported_raises_unsupported_capability_error():
    """Verify calling cancel_inference without CANCELLATION capability raises UnsupportedCapabilityError."""
    provider = MockTestProvider(supported_capabilities={ModelCapability.STREAMING})
    assert provider.supports_cancellation() is False

    with pytest.raises(UnsupportedCapabilityError, match="does not support inference cancellation"):
        await provider.cancel_inference("req-12345")


@pytest.mark.asyncio
async def test_cancel_inference_advertised_unimplemented_raises_not_implemented_error():
    """Verify calling cancel_inference on provider advertising but omitting implementation raises NotImplementedError."""
    provider = IncompleteCapabilityProvider()
    assert provider.supports_cancellation() is True

    with pytest.raises(
        NotImplementedError,
        match="advertises ModelCapability.CANCELLATION but did not implement cancel_inference",
    ):
        await provider.cancel_inference("any-req")


@pytest.mark.asyncio
async def test_cancel_inference_overridden_success():
    """Verify calling cancel_inference succeeds when provider advertises and implements it."""
    provider = MockTestProvider(
        supported_capabilities={ModelCapability.STREAMING, ModelCapability.CANCELLATION}
    )
    assert provider.supports_cancellation() is True

    result = await provider.cancel_inference("req-12345")
    assert result is True
    assert "req-12345" in provider.cancelled_requests


class IncompleteCapabilityProvider(ModelProvider):
    """Provider that advertises optional capabilities but fails to override the methods."""

    @property
    def provider_id(self) -> str:
        return "incomplete-provider"

    @property
    def provider_type(self) -> ProviderType | str:
        return ProviderType.MOCK

    @property
    def display_name(self) -> str:
        return "Incomplete Capability Provider"

    def get_supported_capabilities(self) -> set[ModelCapability]:
        return {
            ModelCapability.STREAMING,
            ModelCapability.MODEL_UNLOADING,
            ModelCapability.CANCELLATION,
        }

    async def check_health(self) -> ProviderHealth:
        return ProviderHealth(provider_id=self.provider_id, status=ProviderStatus.ONLINE)

    async def discover_models(self) -> list[ModelDescriptor]:
        return []

    async def stream_inference(self, request: InferenceRequest) -> AsyncIterator[TokenDelta]:
        yield TokenDelta(finish_reason=FinishReason.STOP)
