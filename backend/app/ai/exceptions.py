"""Domain exception hierarchy for the AI provider and model subsystem."""

from typing import Any

from fastapi import status

from app.core.errors import OrbitError


class AIError(OrbitError):
    """Base exception for all AI provider and inference errors."""

    def __init__(
        self,
        message: str,
        code: str = "AI_ERROR",
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message=message, code=code, status_code=status_code, details=details)


class ProviderUnavailableError(AIError):
    """Raised when an inference provider daemon or service is unreachable or offline."""

    def __init__(
        self,
        message: str = "Inference provider is unavailable or offline",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="PROVIDER_UNAVAILABLE",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            details=details,
        )


class ProviderNotFoundError(AIError):
    """Raised when an operation requests an unregistered or unknown provider."""

    def __init__(
        self,
        message: str = "Requested provider is not registered",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="PROVIDER_NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
            details=details,
        )


class DuplicateProviderError(AIError):
    """Raised when attempting to register a provider with an already registered identifier."""

    def __init__(
        self,
        message: str = "Provider with this identifier is already registered",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="DUPLICATE_PROVIDER",
            status_code=status.HTTP_409_CONFLICT,
            details=details,
        )


class ModelNotFoundError(AIError):
    """Raised when a requested model is not found in any registered provider."""

    def __init__(
        self,
        message: str = "Requested model not found",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="MODEL_NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
            details=details,
        )


class DuplicateModelError(AIError):
    """Raised when duplicate conflicting model identifiers are discovered across providers."""

    def __init__(
        self,
        message: str = "Conflicting duplicate model identifier detected across providers",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="DUPLICATE_MODEL",
            status_code=status.HTTP_409_CONFLICT,
            details=details,
        )


class AmbiguousModelError(AIError):
    """Raised when an un-prefixed model identifier matches multiple providers."""

    def __init__(
        self,
        message: str = "Model identifier is ambiguous across providers; use canonical provider ID",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="AMBIGUOUS_MODEL_ID",
            status_code=status.HTTP_400_BAD_REQUEST,
            details=details,
        )


class InvalidInferenceRequestError(AIError):
    """Raised when an inference request payload is invalid or malformed."""

    def __init__(
        self,
        message: str = "Invalid inference request",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="INVALID_INFERENCE_REQUEST",
            status_code=status.HTTP_400_BAD_REQUEST,
            details=details,
        )


class UnsupportedCapabilityError(AIError):
    """Raised when an operation is requested on a provider that does not support it."""

    def __init__(
        self,
        message: str = "The requested capability is not supported by this provider",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="UNSUPPORTED_CAPABILITY",
            status_code=status.HTTP_400_BAD_REQUEST,
            details=details,
        )


class InferenceGenerationError(AIError):
    """Raised when an inference generation failure occurs during streaming or execution."""

    def __init__(
        self,
        message: str = "Inference generation failed",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="INFERENCE_GENERATION_FAILED",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            details=details,
        )


class InferenceTimeoutError(AIError):
    """Raised when inference generation exceeds configured execution deadlines."""

    def __init__(
        self,
        message: str = "Inference generation timed out",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="INFERENCE_TIMEOUT",
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            details=details,
        )


class InsufficientMemoryError(AIError):
    """Raised when available memory cannot satisfy minimum model or context requirements."""

    def __init__(
        self,
        message: str = "Insufficient memory available to admit model execution",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="INSUFFICIENT_MEMORY",
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            details=details,
        )


class AdmissionDeferredError(AIError):
    """Raised when request must wait for active leases to drain or pressure to subside."""

    def __init__(
        self,
        message: str = "Admission deferred due to in-flight leases or transient pressure",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="ADMISSION_DEFERRED",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            details=details,
        )


class MemoryPressureCriticalError(AIError):
    """Raised when the host is under critical memory pressure and cannot accept work."""

    def __init__(
        self,
        message: str = "System is under critical memory pressure; model admission blocked",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="SYSTEM_MEMORY_PRESSURE_CRITICAL",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            details=details,
        )


class ContextWindowExceededError(AIError):
    """Raised when prompt tokens exceed allocated context or input budget."""

    def __init__(
        self,
        message: str = "Input prompt exceeds allocated context window capacity",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="CONTEXT_WINDOW_EXCEEDED",
            status_code=status.HTTP_400_BAD_REQUEST,
            details=details,
        )


class ConversationNotFoundError(AIError):
    """Raised when the specified conversation ID does not exist."""

    def __init__(
        self,
        message: str = "Conversation not found",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="CONVERSATION_NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
            details=details,
        )
