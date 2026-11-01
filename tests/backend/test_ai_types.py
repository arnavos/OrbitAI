"""Unit tests for AI core data types, schemas, and validation contracts."""

import pytest
from pydantic import ValidationError

from app.ai.types import (
    ChatMessage,
    FinishReason,
    GenerationConfig,
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

# ==============================================================================
# ModelDescriptor Tests
# ==============================================================================


def test_model_descriptor_valid_minimal():
    """Verify minimal valid ModelDescriptor instantiation."""
    desc = ModelDescriptor(
        id="mock/test-model-3b",
        name="Test Model 3B",
        provider_type=ProviderType.MOCK,
        context_window_capacity=8192,
    )
    assert desc.id == "mock/test-model-3b"
    assert desc.name == "Test Model 3B"
    assert desc.provider_type == ProviderType.MOCK
    assert desc.context_window_capacity == 8192
    assert desc.parameter_size is None
    assert desc.quantization is None
    assert desc.estimated_memory_bytes is None
    assert desc.capabilities == set()
    assert desc.availability_status == ModelAvailabilityStatus.AVAILABLE
    assert desc.provider_metadata == {}
    assert desc.native_model_id == "test-model-3b"


def test_model_descriptor_native_id_auto_population():
    """Verify native_model_id population for bare and prefixed IDs."""
    # Bare ID
    d1 = ModelDescriptor(
        id="llama3.2:3b",
        name="Llama 3.2 3B",
        provider_type=ProviderType.OLLAMA,
        context_window_capacity=8192,
    )
    assert d1.id == "llama3.2:3b"
    assert d1.native_model_id == "llama3.2:3b"

    # Prefixed ID
    d2 = ModelDescriptor(
        id="ollama/llama3.2:3b",
        name="Llama 3.2 3B",
        provider_type=ProviderType.OLLAMA,
        context_window_capacity=8192,
    )
    assert d2.id == "ollama/llama3.2:3b"
    assert d2.native_model_id == "llama3.2:3b"

    # Explicit native ID preserved
    d3 = ModelDescriptor(
        id="custom-registry-id",
        name="Custom Model",
        provider_type=ProviderType.CUSTOM,
        context_window_capacity=4096,
        native_model_id="internal-v1",
    )
    assert d3.id == "custom-registry-id"
    assert d3.native_model_id == "internal-v1"


def test_model_descriptor_as_canonical_namespacing():
    """Verify as_canonical converts bare IDs to canonical '<provider_id>/<native_id>'."""
    bare = ModelDescriptor(
        id="llama3.2:3b",
        name="Llama 3.2 3B",
        provider_type=ProviderType.OLLAMA,
        context_window_capacity=8192,
    )
    canonical = bare.as_canonical("ollama")
    assert canonical.id == "ollama/llama3.2:3b"
    assert canonical.native_model_id == "llama3.2:3b"
    # Ensure original instance is not mutated
    assert bare.id == "llama3.2:3b"


def test_model_descriptor_as_canonical_no_double_prefix():
    """Verify as_canonical avoids double-prefixing already-qualified IDs."""
    already_canonical = ModelDescriptor(
        id="ollama/llama3.2:3b",
        name="Llama 3.2 3B",
        provider_type=ProviderType.OLLAMA,
        context_window_capacity=8192,
    )
    result = already_canonical.as_canonical("ollama")
    assert result.id == "ollama/llama3.2:3b"
    assert result.native_model_id == "llama3.2:3b"
    assert result.provider_id == "ollama"


def test_model_descriptor_slash_containing_native_id():
    """Verify native model IDs containing '/' preserve full path and namespace cleanly."""
    desc = ModelDescriptor(
        id="meta-llama/Llama-3.2-3B",
        name="Llama 3.2 3B Instruct",
        provider_type=ProviderType.OLLAMA,
        context_window_capacity=8192,
    )
    # Native ID must preserve organization/model without stripping the prefix
    assert desc.id == "meta-llama/Llama-3.2-3B"
    assert desc.native_model_id == "meta-llama/Llama-3.2-3B"

    # Canonicalization prefixes with provider_id while keeping full native path
    canonical = desc.as_canonical("ollama")
    assert canonical.id == "ollama/meta-llama/Llama-3.2-3B"
    assert canonical.native_model_id == "meta-llama/Llama-3.2-3B"
    assert canonical.provider_id == "ollama"

    # Re-canonicalizing is idempotent and does not double-prefix
    idempotent = canonical.as_canonical("ollama")
    assert idempotent.id == "ollama/meta-llama/Llama-3.2-3B"
    assert idempotent.native_model_id == "meta-llama/Llama-3.2-3B"


def test_model_descriptor_multi_slash_native_id():
    """Verify deeply nested native model IDs with multiple slashes are preserved."""
    desc = ModelDescriptor(
        id="deep/nested/org/model-v1:latest",
        name="Nested Model",
        provider_type=ProviderType.CUSTOM,
        context_window_capacity=4096,
    )
    assert desc.native_model_id == "deep/nested/org/model-v1:latest"

    canonical = desc.as_canonical("custom-backend")
    assert canonical.id == "custom-backend/deep/nested/org/model-v1:latest"
    assert canonical.native_model_id == "deep/nested/org/model-v1:latest"
    assert canonical.provider_id == "custom-backend"


def test_model_descriptor_as_canonical_mismatched_provider_rejected():
    """Verify as_canonical rejects silently reassigning a model to a different provider."""
    canonical = ModelDescriptor(
        id="ollama/llama3.2:3b",
        name="Llama 3.2 3B",
        provider_type=ProviderType.OLLAMA,
        context_window_capacity=8192,
    ).as_canonical("ollama")

    with pytest.raises(ValueError, match="already qualified with provider 'ollama'"):
        canonical.as_canonical("llamacpp")

    slash_canonical = ModelDescriptor(
        id="meta-llama/Llama-3.2-3B",
        name="Llama 3.2",
        provider_type=ProviderType.OLLAMA,
        context_window_capacity=8192,
    ).as_canonical("ollama")

    with pytest.raises(ValueError, match="already qualified with provider 'ollama'"):
        slash_canonical.as_canonical("vllm")


def test_model_descriptor_valid_full():
    """Verify full ModelDescriptor with all optional fields."""
    desc = ModelDescriptor(
        id="ollama/llama3.2:3b",
        name="Llama 3.2 3B Instruct",
        provider_type=ProviderType.OLLAMA,
        model_family="llama",
        parameter_size="3.2B",
        quantization="Q4_K_M",
        context_window_capacity=131072,
        estimated_memory_bytes=2_400_000_000,
        capabilities={ModelCapability.STREAMING, ModelCapability.CANCELLATION},
        availability_status=ModelAvailabilityStatus.LOADED,
        provider_metadata={"digest": "abc12345", "format": "gguf"},
    )
    assert desc.has_capability(ModelCapability.STREAMING) is True
    assert desc.has_capability(ModelCapability.VISION) is False
    assert desc.estimated_memory_bytes == 2_400_000_000
    assert desc.provider_metadata["format"] == "gguf"


def test_model_descriptor_invalid_context_capacity():
    """Verify non-positive context_window_capacity is rejected."""
    with pytest.raises(ValidationError, match="greater than 0"):
        ModelDescriptor(
            id="mock/model-1",
            name="Model 1",
            provider_type=ProviderType.MOCK,
            context_window_capacity=0,
        )

    with pytest.raises(ValidationError, match="greater than 0"):
        ModelDescriptor(
            id="mock/model-1",
            name="Model 1",
            provider_type=ProviderType.MOCK,
            context_window_capacity=-1024,
        )


def test_model_descriptor_invalid_memory_bytes():
    """Verify negative estimated_memory_bytes is rejected."""
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        ModelDescriptor(
            id="mock/model-1",
            name="Model 1",
            provider_type=ProviderType.MOCK,
            context_window_capacity=4096,
            estimated_memory_bytes=-500,
        )


def test_model_descriptor_invalid_id_characters():
    """Verify invalid characters in model ID are rejected."""
    with pytest.raises(ValidationError, match="contains invalid characters"):
        ModelDescriptor(
            id="mock/model with spaces!",
            name="Model 1",
            provider_type=ProviderType.MOCK,
            context_window_capacity=4096,
        )


def test_model_descriptor_empty_name_rejected():
    """Verify empty or whitespace-only name is rejected."""
    with pytest.raises(ValidationError, match="Model name cannot be empty"):
        ModelDescriptor(
            id="mock/model-1",
            name="   ",
            provider_type=ProviderType.MOCK,
            context_window_capacity=4096,
        )


def test_model_descriptor_serialization_roundtrip():
    """Verify serialization to dict and JSON and roundtrip restoration."""
    desc = ModelDescriptor(
        id="mlx/qwen2.5-7b",
        name="Qwen 2.5 7B",
        provider_type=ProviderType.MLX,
        context_window_capacity=32768,
        capabilities={ModelCapability.STREAMING, ModelCapability.STRUCTURED_OUTPUT},
    )
    raw_dict = desc.model_dump(mode="json")
    assert raw_dict["id"] == "mlx/qwen2.5-7b"
    assert "streaming" in raw_dict["capabilities"]
    assert "structured_output" in raw_dict["capabilities"]

    json_str = desc.model_dump_json()
    restored = ModelDescriptor.model_validate_json(json_str)
    assert restored.id == desc.id
    assert restored.capabilities == desc.capabilities


# ==============================================================================
# GenerationConfig Tests
# ==============================================================================


def test_generation_config_defaults():
    """Verify default GenerationConfig ranges and values."""
    config = GenerationConfig()
    assert config.temperature == 0.7
    assert config.top_p == 0.9
    assert config.max_output_tokens is None
    assert config.stop_sequences == []
    assert config.timeout_seconds == 60.0
    assert config.extra_params == {}


def test_generation_config_invalid_temperature():
    """Verify out-of-range temperatures are rejected."""
    with pytest.raises(ValidationError, match="less than or equal to 2"):
        GenerationConfig(temperature=2.5)

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        GenerationConfig(temperature=-0.1)


def test_generation_config_invalid_top_p():
    """Verify out-of-range top_p is rejected."""
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        GenerationConfig(top_p=1.2)

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        GenerationConfig(top_p=-0.01)


def test_generation_config_invalid_max_tokens():
    """Verify non-positive max_output_tokens is rejected."""
    with pytest.raises(ValidationError, match="greater than 0"):
        GenerationConfig(max_output_tokens=0)

    with pytest.raises(ValidationError, match="greater than 0"):
        GenerationConfig(max_output_tokens=-10)


def test_generation_config_stop_sequences_validation():
    """Verify stop_sequences are validated."""
    valid = GenerationConfig(stop_sequences=["<|end|>", "User:"])
    assert len(valid.stop_sequences) == 2

    with pytest.raises(ValidationError, match="must be non-empty strings"):
        GenerationConfig(stop_sequences=[""])


# ==============================================================================
# ChatMessage Tests
# ==============================================================================


def test_chat_message_valid_roles():
    """Verify ChatMessage construction with all valid roles."""
    for role in [MessageRole.SYSTEM, MessageRole.USER, MessageRole.ASSISTANT, MessageRole.TOOL]:
        msg = ChatMessage(role=role, content=f"Hello from {role.value}")
        assert msg.role == role
        assert "Hello" in msg.content


def test_chat_message_invalid_role():
    """Verify invalid roles are rejected."""
    with pytest.raises(ValidationError):
        ChatMessage(role="invalid_role", content="Hello")  # type: ignore[arg-type]


def test_chat_message_empty_content_rejected():
    """Verify empty or pure whitespace content is rejected."""
    with pytest.raises(ValidationError, match="cannot be empty or purely whitespace"):
        ChatMessage(role=MessageRole.USER, content="")

    with pytest.raises(ValidationError, match="cannot be empty or purely whitespace"):
        ChatMessage(role=MessageRole.USER, content="   \n\t  ")


# ==============================================================================
# InferenceRequest Tests
# ==============================================================================


def test_inference_request_valid():
    """Verify valid InferenceRequest instantiation."""
    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content="You are ORBIT."),
        ChatMessage(role=MessageRole.USER, content="Hello."),
    ]
    req = InferenceRequest(model_id="mock/model-1", messages=messages)
    assert req.model_id == "mock/model-1"
    assert len(req.messages) == 2
    assert req.request_id is not None
    assert req.correlation_id is not None


def test_inference_request_empty_messages_rejected():
    """Verify InferenceRequest with empty messages list is rejected."""
    with pytest.raises(ValidationError, match="at least 1"):
        InferenceRequest(model_id="mock/model-1", messages=[])


# ==============================================================================
# TokenDelta & TokenUsage Tests
# ==============================================================================


def test_token_usage_total_computation():
    """Verify automatic total_tokens calculation if prompt and completion are supplied."""
    usage = TokenUsage(
        prompt_tokens=10,
        completion_tokens=25,
        source=UsageSource.REPORTED,
    )
    assert usage.total_tokens == 35
    assert usage.source == UsageSource.REPORTED


def test_token_delta_normal_chunk():
    """Verify standard text delta."""
    delta = TokenDelta(text="Hello world")
    assert delta.text == "Hello world"
    assert delta.finish_reason is None
    assert delta.usage is None


def test_token_delta_completion_chunk():
    """Verify stream terminal chunk with usage metadata."""
    delta = TokenDelta(
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(prompt_tokens=5, completion_tokens=10),
    )
    assert delta.finish_reason == FinishReason.STOP
    assert delta.usage is not None
    assert delta.usage.total_tokens == 15


def test_token_delta_error_state_auto_populates_detail():
    """Verify error state auto-populates ErrorDetail if omitted."""
    delta = TokenDelta(finish_reason=FinishReason.ERROR)
    assert delta.error_detail is not None
    assert delta.error_detail.code == "INFERENCE_STREAM_ERROR"


def test_provider_health_valid():
    """Verify ProviderHealth attributes."""
    health = ProviderHealth(
        provider_id="mock-provider",
        status=ProviderStatus.ONLINE,
        latency_ms=1.5,
        message="Daemon responsive",
    )
    assert health.status == ProviderStatus.ONLINE
    assert health.latency_ms == 1.5
    assert health.last_checked_at is not None
