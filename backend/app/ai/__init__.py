"""ORBIT AI Provider and Model Abstraction Layer."""

from app.ai.exceptions import (
    AIError,
    AmbiguousModelError,
    DuplicateModelError,
    DuplicateProviderError,
    InferenceGenerationError,
    InferenceTimeoutError,
    InvalidInferenceRequestError,
    ModelNotFoundError,
    ProviderNotFoundError,
    ProviderUnavailableError,
    UnsupportedCapabilityError,
)
from app.ai.prompts import ORBIT_SYSTEM_PROMPT, resolve_system_prompt
from app.ai.provider import ModelProvider
from app.ai.providers import OllamaProvider
from app.ai.registry import DiscoveryResult, ModelRegistry
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

__all__ = [
    "AIError",
    "AmbiguousModelError",
    "ChatMessage",
    "DiscoveryResult",
    "DuplicateModelError",
    "DuplicateProviderError",
    "FinishReason",
    "GenerationConfig",
    "InferenceGenerationError",
    "InferenceRequest",
    "InferenceTimeoutError",
    "InvalidInferenceRequestError",
    "MessageRole",
    "ModelAvailabilityStatus",
    "ModelCapability",
    "ModelDescriptor",
    "ModelNotFoundError",
    "ModelProvider",
    "ModelRegistry",
    "OllamaProvider",
    "ORBIT_SYSTEM_PROMPT",
    "ProviderHealth",
    "ProviderNotFoundError",
    "ProviderStatus",
    "ProviderType",
    "ProviderUnavailableError",
    "resolve_system_prompt",
    "TokenDelta",
    "TokenUsage",
    "UnsupportedCapabilityError",
    "UsageSource",
]
