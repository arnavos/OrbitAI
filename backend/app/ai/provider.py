"""Abstract base class defining the provider contract for all ORBIT inference backends."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.ai.exceptions import UnsupportedCapabilityError
from app.ai.types import (
    InferenceRequest,
    ModelCapability,
    ModelDescriptor,
    ProviderHealth,
    ProviderType,
    TokenDelta,
)


class ModelProvider(ABC):
    """Abstract Base Class for local and remote inference providers.

    All model inference backends (Ollama, MLX, llama.cpp, mock test fixtures)
    must implement this contract. This guarantees that upper conversational layers,
    memory managers, and user interfaces interact through a unified interface
    without coupling to any specific inference daemon.
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique machine-readable identifier for this provider instance (e.g. 'ollama-local')."""
        ...

    @property
    @abstractmethod
    def provider_type(self) -> ProviderType | str:
        """Categorical provider type classification (e.g. ProviderType.OLLAMA)."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable display name for UI rendering (e.g. 'Ollama (Local Daemon)')."""
        ...

    def get_supported_capabilities(self) -> set[ModelCapability]:
        """Return the set of capabilities natively supported by this provider implementation.

        Subclasses override this to advertise supported features.
        Default baseline assumes streaming capability only.
        """
        return {ModelCapability.STREAMING}

    def supports_capability(self, capability: ModelCapability) -> bool:
        """Check if a specific capability is supported by this provider."""
        return capability in self.get_supported_capabilities()

    def supports_unloading(self) -> bool:
        """Convenience method checking if provider supports explicit model eviction."""
        return self.supports_capability(ModelCapability.MODEL_UNLOADING)

    def supports_cancellation(self) -> bool:
        """Convenience method checking if provider supports explicit inference cancellation."""
        return self.supports_capability(ModelCapability.CANCELLATION)

    @abstractmethod
    async def check_health(self) -> ProviderHealth:
        """Probe provider daemon connectivity and return health status.

        Must never raise unhandled exceptions across the boundary; any daemon
        unreachability must be captured in the returned ProviderHealth.
        """
        ...

    @abstractmethod
    async def discover_models(self) -> list[ModelDescriptor]:
        """Discover installed or available models from the provider.

        Raises:
            ProviderUnavailableError: If the provider daemon is offline.
        """
        ...

    @abstractmethod
    async def stream_inference(self, request: InferenceRequest) -> AsyncIterator[TokenDelta]:
        """Stream generated token deltas asynchronously for the given request.

        Raises:
            ModelNotFoundError: If requested model is not found in provider.
            ProviderUnavailableError: If provider daemon is unreachable.
            InferenceGenerationError: If generation aborts unexpectedly.
            InferenceTimeoutError: If execution exceeds configured timeout.
        """
        ...

    async def unload_model(self, model_id: str) -> bool:
        """Explicitly evict a model from memory to reclaim system resources.

        Subclasses that advertise ModelCapability.MODEL_UNLOADING must override
        this method to implement concrete daemon or memory eviction.

        Raises:
            UnsupportedCapabilityError: If provider does not advertise
                ModelCapability.MODEL_UNLOADING.
            NotImplementedError: If provider advertises capability but did not override this method.
            ModelNotFoundError: If requested model is not recognized.
            ProviderUnavailableError: If provider daemon is offline.
        """
        if not self.supports_unloading():
            msg = (
                f"Provider '{self.provider_id}' ({self.provider_type}) "
                "does not support explicit model unloading."
            )
            raise UnsupportedCapabilityError(msg)
        msg = (
            f"Provider '{self.provider_id}' ({self.provider_type}) advertises "
            "ModelCapability.MODEL_UNLOADING but did not implement unload_model()."
        )
        raise NotImplementedError(msg)

    async def cancel_inference(self, request_id: str) -> bool:
        """Cancel an in-flight inference generation request.

        Subclasses that advertise ModelCapability.CANCELLATION must override
        this method to implement concrete generation cancellation.

        Raises:
            UnsupportedCapabilityError: If provider does not advertise ModelCapability.CANCELLATION.
            NotImplementedError: If provider advertises capability but did not override this method.
            ProviderUnavailableError: If provider daemon is offline.
        """
        if not self.supports_cancellation():
            msg = (
                f"Provider '{self.provider_id}' ({self.provider_type}) "
                "does not support inference cancellation."
            )
            raise UnsupportedCapabilityError(msg)
        msg = (
            f"Provider '{self.provider_id}' ({self.provider_type}) advertises "
            "ModelCapability.CANCELLATION but did not implement cancel_inference()."
        )
        raise NotImplementedError(msg)
