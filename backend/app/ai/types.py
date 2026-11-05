"""Core data structures, enums, and schemas for the ORBIT AI provider runtime."""

import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import ErrorDetail

# Regex for model identifier format: e.g. "ollama/llama3.2:3b", "mlx/qwen2.5-7b"
MODEL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-.:/]+$")


class ProviderType(StrEnum):
    """Supported or known provider backend classes."""

    OLLAMA = "ollama"
    MLX = "mlx"
    LLAMA_CPP = "llama_cpp"
    OPENAI_COMPATIBLE = "openai_compatible"
    MOCK = "mock"
    CUSTOM = "custom"


class ModelCapability(StrEnum):
    """Discrete capabilities that can be supported by an AI model or inference provider."""

    STREAMING = "streaming"
    TOOL_CALLING = "tool_calling"
    VISION = "vision"
    AUDIO_INPUT = "audio_input"
    AUDIO_OUTPUT = "audio_output"
    STRUCTURED_OUTPUT = "structured_output"
    CANCELLATION = "cancellation"
    MODEL_UNLOADING = "model_unloading"


class ModelAvailabilityStatus(StrEnum):
    """Operational availability status of a model in the provider."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    LOADING = "loading"
    LOADED = "loaded"
    DOWNLOADING = "downloading"


class ProviderStatus(StrEnum):
    """Health status of an inference provider daemon or service."""

    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


class MessageRole(StrEnum):
    """Role associated with a conversational turn."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(StrEnum):
    """Reason why an inference stream or completion terminated."""

    STOP = "stop"
    LENGTH = "length"
    CANCELLED = "cancelled"
    ERROR = "error"


class UsageSource(StrEnum):
    """Source of token usage accounting metadata."""

    UNKNOWN = "unknown"
    REPORTED = "reported"
    ESTIMATED = "estimated"


class TokenUsage(BaseModel):
    """Token consumption accounting for an inference call."""

    prompt_tokens: int | None = Field(default=None, ge=0, description="Tokens in input prompt")
    completion_tokens: int | None = Field(
        default=None, ge=0, description="Tokens in generated output"
    )
    total_tokens: int | None = Field(
        default=None, ge=0, description="Total tokens evaluated and generated"
    )
    source: UsageSource = Field(
        default=UsageSource.UNKNOWN, description="Accounting provenance (reported or estimated)"
    )

    @model_validator(mode="after")
    def compute_total_if_missing(self) -> "TokenUsage":
        """Compute total_tokens automatically if prompt and completion are provided."""
        if (
            self.total_tokens is None
            and self.prompt_tokens is not None
            and self.completion_tokens is not None
        ):
            self.total_tokens = self.prompt_tokens + self.completion_tokens
        return self


class TokenDelta(BaseModel):
    """Incremental streaming token event emitted during generation."""

    text: str = Field(default="", description="Incremental textual piece")
    finish_reason: FinishReason | None = Field(
        default=None, description="Terminal status reason if stream completed"
    )
    usage: TokenUsage | None = Field(
        default=None, description="Usage metrics populated on stream completion"
    )
    error_detail: ErrorDetail | None = Field(
        default=None, description="Error payload if stream encountered an error"
    )

    @model_validator(mode="after")
    def validate_error_state(self) -> "TokenDelta":
        """Ensure error states include appropriate finish reason or detail."""
        if self.finish_reason == FinishReason.ERROR and self.error_detail is None:
            self.error_detail = ErrorDetail(
                code="INFERENCE_STREAM_ERROR",
                message="Inference stream terminated with unspecified error",
            )
        return self


class ChatMessage(BaseModel):
    """Normalized conversational message turn."""

    role: MessageRole = Field(..., description="Conversational role")
    content: str = Field(..., description="Text content of the message")
    name: str | None = Field(default=None, description="Optional author or tool identifier")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary message metadata")

    @field_validator("content")
    @classmethod
    def validate_content_not_empty(cls, v: str) -> str:
        """Ensure content is a non-empty string."""
        stripped = v.strip()
        if not stripped:
            msg = "Message content cannot be empty or purely whitespace."
            raise ValueError(msg)
        return v


class GenerationConfig(BaseModel):
    """Hyperparameters configuring model inference."""

    temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, description="Sampling randomness between 0.0 and 2.0"
    )
    top_p: float = Field(
        default=0.9, ge=0.0, le=1.0, description="Nucleus sampling probability between 0.0 and 1.0"
    )
    max_output_tokens: int | None = Field(
        default=None, gt=0, description="Optional upper bound on generated tokens"
    )
    stop_sequences: list[str] = Field(
        default_factory=list, description="Sequences where generation will halt"
    )
    timeout_seconds: float = Field(
        default=60.0, gt=0.0, description="Inference execution timeout in seconds"
    )
    extra_params: dict[str, Any] = Field(
        default_factory=dict, description="Provider-specific knobs isolated from common config"
    )

    @field_validator("stop_sequences")
    @classmethod
    def validate_stop_sequences(cls, v: list[str]) -> list[str]:
        """Ensure stop sequences contain non-empty strings."""
        cleaned = []
        for seq in v:
            if not isinstance(seq, str) or not seq:
                msg = "Stop sequences must be non-empty strings."
                raise ValueError(msg)
            cleaned.append(seq)
        return cleaned


class ModelDescriptor(BaseModel):
    """Normalized metadata describing an AI model provided by an inference backend."""

    id: str = Field(
        ..., min_length=1, description="Unique model identifier (e.g. 'ollama/llama3.2:3b')"
    )
    name: str = Field(..., min_length=1, description="Human-readable model label")
    provider_type: ProviderType | str = Field(..., description="Provider backend classification")
    model_family: str | None = Field(
        default=None, description="Architecture family (e.g. 'llama', 'qwen')"
    )
    parameter_size: str | None = Field(
        default=None, description="Parameter count if known (e.g. '3.2B')"
    )
    quantization: str | None = Field(
        default=None, description="Quantization scheme (e.g. 'Q4_K_M')"
    )
    context_window_capacity: int | None = Field(
        default=None, gt=0, description="Verified maximum context window in tokens if known"
    )
    estimated_memory_bytes: int | None = Field(
        default=None, ge=0, description="Estimated unified memory requirement in bytes"
    )
    capabilities: set[ModelCapability] = Field(
        default_factory=set, description="Set of verified capabilities"
    )
    availability_status: ModelAvailabilityStatus = Field(
        default=ModelAvailabilityStatus.AVAILABLE, description="Runtime readiness of the model"
    )
    native_model_id: str | None = Field(
        default=None,
        description="Original un-prefixed native identifier in the provider (e.g. 'llama3.2:3b')",
    )
    provider_id: str | None = Field(
        default=None,
        description="Owning provider instance identifier if canonicalized",
    )
    provider_metadata: dict[str, Any] = Field(
        default_factory=dict, description="Untrusted provider-specific attributes"
    )

    @model_validator(mode="after")
    def populate_native_model_id(self) -> "ModelDescriptor":
        """Ensure native_model_id is populated if not explicitly set.

        Preserves full native IDs (including those with slashes, such as
        'organization/model:latest') unless the ID is already prefixed with a known
        provider_id or provider_type.
        """
        if self.native_model_id is not None:
            return self

        # Check if ID starts with provider_id prefix if provider_id was supplied
        if self.provider_id and self.id.startswith(f"{self.provider_id}/"):
            self.native_model_id = self.id[len(self.provider_id) + 1 :]
            return self

        # Check if ID starts with provider_type prefix (e.g. 'mock/', 'ollama/')
        p_type_val = (
            self.provider_type.value
            if hasattr(self.provider_type, "value")
            else str(self.provider_type)
        )
        if p_type_val and self.id.startswith(f"{p_type_val}/"):
            self.native_model_id = self.id[len(p_type_val) + 1 :]
            return self

        # Otherwise, the entire id represents the native model ID (slash-containing or bare)
        self.native_model_id = self.id
        return self

    @field_validator("id")
    @classmethod
    def validate_id_format(cls, v: str) -> str:
        """Validate model identifier format."""
        cleaned = v.strip()
        if not cleaned:
            msg = "Model identifier cannot be empty."
            raise ValueError(msg)
        if not MODEL_ID_PATTERN.match(cleaned):
            msg = f"Model identifier '{cleaned}' contains invalid characters."
            raise ValueError(msg)
        return cleaned

    @field_validator("name")
    @classmethod
    def validate_name_not_empty(cls, v: str) -> str:
        """Validate model display name is not empty."""
        cleaned = v.strip()
        if not cleaned:
            msg = "Model name cannot be empty."
            raise ValueError(msg)
        return cleaned

    def has_capability(self, capability: ModelCapability) -> bool:
        """Check if this model supports a given capability."""
        return capability in self.capabilities

    def as_canonical(self, provider_id: str) -> "ModelDescriptor":
        """Return a copy of this descriptor with a canonical '<provider_id>/<native_id>' ID.

        Ensures deterministic namespacing and prevents accidental double-prefixing.
        Rejects attempting to reassign a descriptor already qualified for a different provider.

        Raises:
            ValueError: If the descriptor is already qualified with a different provider prefix.
        """
        prefix = f"{provider_id}/"

        if self.id.startswith(prefix):
            # Already canonicalized for this exact provider
            canonical_id = self.id
            if self.native_model_id and not self.native_model_id.startswith(prefix):
                native_id = self.native_model_id
            else:
                native_id = self.id[len(prefix) :]
        else:
            # Check for provider prefix reassignment mismatch
            if self.provider_id and self.provider_id != provider_id:
                msg = (
                    f"ModelDescriptor '{self.id}' is already qualified with provider "
                    f"'{self.provider_id}' and cannot be reassigned to '{provider_id}'."
                )
                raise ValueError(msg)

            # Check if self.id was already qualified with another prefix (id != native_model_id)
            if (
                self.native_model_id
                and "/" in self.id
                and self.id.endswith(f"/{self.native_model_id}")
            ):
                existing_p = self.id[: -len(f"/{self.native_model_id}")]
                if existing_p != provider_id:
                    msg = (
                        f"ModelDescriptor '{self.id}' is already qualified with provider "
                        f"'{existing_p}' and cannot be reassigned to '{provider_id}'."
                    )
                    raise ValueError(msg)

            native_id = self.native_model_id or self.id
            canonical_id = f"{prefix}{native_id}"

        return self.model_copy(
            update={
                "id": canonical_id,
                "native_model_id": native_id,
                "provider_id": provider_id,
            }
        )


class InferenceRequest(BaseModel):
    """Complete specification of an inference execution request."""

    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), description="Unique request tracing identifier"
    )
    model_id: str = Field(..., min_length=1, description="Target model identifier")
    messages: list[ChatMessage] = Field(
        ..., min_length=1, description="Ordered conversational turns"
    )
    config: GenerationConfig = Field(
        default_factory=GenerationConfig, description="Generation hyperparameters"
    )
    correlation_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), description="Cross-system correlation ID"
    )

    @field_validator("messages")
    @classmethod
    def validate_messages_not_empty(cls, v: list[ChatMessage]) -> list[ChatMessage]:
        """Ensure inference request has at least one message."""
        if not v:
            msg = "InferenceRequest must contain at least one ChatMessage."
            raise ValueError(msg)
        return v


class ProviderHealth(BaseModel):
    """Health and connectivity status of an inference provider daemon."""

    provider_id: str = Field(..., description="Provider unique identifier")
    status: ProviderStatus = Field(..., description="Operational status")
    latency_ms: float | None = Field(
        default=None, ge=0.0, description="Ping roundtrip latency in ms"
    )
    message: str | None = Field(default=None, description="Diagnostic status or error message")
    last_checked_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="ISO 8601 UTC timestamp of last probe",
    )
