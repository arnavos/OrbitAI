"""Model registry coordinating provider lifecycles, health checks, and cross-provider discovery."""

import asyncio
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.ai.exceptions import (
    AmbiguousModelError,
    DuplicateProviderError,
    ModelNotFoundError,
    ProviderNotFoundError,
)
from app.ai.provider import ModelProvider
from app.ai.types import (
    ModelDescriptor,
    ProviderHealth,
    ProviderStatus,
)
from app.core.logging import logger


class DiscoveryResult(BaseModel):
    """Aggregate result from discovering models across all registered providers."""

    models: list[ModelDescriptor] = Field(
        default_factory=list, description="Successfully discovered canonical model descriptors"
    )
    errors: dict[str, str] = Field(
        default_factory=dict, description="Diagnostics for providers that failed discovery"
    )
    discovered_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="ISO 8601 UTC timestamp of discovery execution",
    )


class ModelRegistry:
    """Central registry and discovery coordinator for model inference backends.

    Maintains the collection of active inference providers in-memory.
    Provides cross-provider model resolution and isolates failures so that
    an offline or misbehaving provider does not degrade available models
    from healthy providers.

    Note on Synchronization:
    The internal `self._lock` is an `asyncio.Lock` providing coroutine-level
    synchronization within the single-threaded asyncio event loop. It prevents
    interleaving across `await` points during provider registration or unregistration.
    It does not provide general OS thread-safe locking.
    """

    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self._lock = asyncio.Lock()

    async def register_provider(self, provider: ModelProvider) -> None:
        """Register a new inference provider.

        Raises:
            DuplicateProviderError: If a provider with the same provider_id is already registered.
            ValueError: If provider is invalid.
        """
        if not provider or not provider.provider_id:
            msg = "Provider must have a valid non-empty provider_id."
            raise ValueError(msg)

        async with self._lock:
            if provider.provider_id in self._providers:
                msg = f"Provider '{provider.provider_id}' is already registered."
                raise DuplicateProviderError(msg)
            self._providers[provider.provider_id] = provider
            logger.info(
                f"Registered AI model provider: '{provider.provider_id}' ({provider.provider_type})"
            )

    async def unregister_provider(self, provider_id: str) -> bool:
        """Remove a provider from the registry.

        Returns True if provider was removed, False if it was not registered.
        """
        async with self._lock:
            removed = self._providers.pop(provider_id, None)
            if removed:
                logger.info(f"Unregistered AI model provider: '{provider_id}'")
                return True
            return False

    def get_provider(self, provider_id: str) -> ModelProvider:
        """Retrieve a registered provider by its identifier.

        Raises:
            ProviderNotFoundError: If provider_id is not registered.
        """
        provider = self._providers.get(provider_id)
        if not provider:
            msg = f"Provider '{provider_id}' is not registered."
            raise ProviderNotFoundError(msg)
        return provider

    def list_providers(self) -> list[ModelProvider]:
        """Return a snapshot list of all currently registered providers."""
        return list(self._providers.values())

    async def check_all_health(self) -> dict[str, ProviderHealth]:
        """Probe health across all registered providers concurrently.

        Returns a dictionary mapping provider_id to its ProviderHealth status.
        Never raises exceptions; provider probe failures are safely captured.
        """
        providers = self.list_providers()
        if not providers:
            return {}

        tasks = [p.check_health() for p in providers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        health_map: dict[str, ProviderHealth] = {}
        for provider, res in zip(providers, results, strict=False):
            if isinstance(res, Exception):
                logger.warning(f"Health check failed for provider '{provider.provider_id}': {res}")
                health_map[provider.provider_id] = ProviderHealth(
                    provider_id=provider.provider_id,
                    status=ProviderStatus.OFFLINE,
                    message=f"Health probe exception: {res}",
                )
            elif isinstance(res, ProviderHealth):
                health_map[provider.provider_id] = res
            else:
                health_map[provider.provider_id] = ProviderHealth(
                    provider_id=provider.provider_id,
                    status=ProviderStatus.DEGRADED,
                    message="Unexpected health probe return type",
                )

        return health_map

    async def discover_models(self) -> DiscoveryResult:
        """Discover models across all registered providers in parallel.

        Deterministic Identity & Namespacing:
        Every discovered model is normalized into its canonical provider-qualified ID:
        '<provider_id>/<native_model_id>'.
        If two distinct providers expose models with identical native names, both models
        are preserved with distinct canonical IDs, eliminating race conditions based
        on discovery completion order.

        Failure Isolation Invariant:
        If one provider fails or is offline, its error is captured in `errors`
        without raising an exception, allowing all healthy providers' models
        to be returned.
        """
        providers = self.list_providers()
        if not providers:
            return DiscoveryResult()

        tasks = [p.discover_models() for p in providers]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        discovered_models: list[ModelDescriptor] = []
        discovery_errors: dict[str, str] = {}
        seen_canonical_ids: set[str] = set()

        for provider, result in zip(providers, raw_results, strict=False):
            p_id = provider.provider_id
            if isinstance(result, Exception):
                err_msg = str(result) or type(result).__name__
                discovery_errors[p_id] = err_msg
                logger.warning(f"Model discovery failed for provider '{p_id}': {err_msg}")
            elif isinstance(result, list):
                for model in result:
                    if not isinstance(model, ModelDescriptor):
                        continue

                    # Normalize into canonical '<provider_id>/<native_id>' descriptor
                    canonical_model = model.as_canonical(p_id)

                    if canonical_model.id in seen_canonical_ids:
                        logger.warning(
                            f"Duplicate model ID '{canonical_model.id}' within provider '{p_id}', "
                            "ignoring duplicate"
                        )
                        continue

                    seen_canonical_ids.add(canonical_model.id)
                    discovered_models.append(canonical_model)
            else:
                discovery_errors[p_id] = "Unexpected discovery return type"

        return DiscoveryResult(models=discovered_models, errors=discovery_errors)

    async def get_model(self, model_id: str) -> ModelDescriptor:
        """Locate and return the ModelDescriptor for the requested model_id.

        Supports both canonical '<provider_id>/<native_id>' IDs and bare or
        slash-containing native model IDs.
        If a native ID matches multiple providers, raises AmbiguousModelError.

        Raises:
            AmbiguousModelError: If a native model ID matches multiple providers.
            ModelNotFoundError: If the model cannot be found in any registered provider.
        """
        discovery = await self.discover_models()

        # 1. Exact match on canonical ID
        for model in discovery.models:
            if model.id == model_id:
                return model

        # 2. Match on native_model_id (bare or slash-containing native ID)
        native_matches = [m for m in discovery.models if m.native_model_id == model_id]
        if len(native_matches) == 1:
            return native_matches[0]
        elif len(native_matches) > 1:
            matching_ids = [m.id for m in native_matches]
            msg = (
                f"Model identifier '{model_id}' is ambiguous across multiple providers: "
                f"{matching_ids}. Specify the canonical '<provider_id>/<model_id>' identifier."
            )
            raise AmbiguousModelError(msg, details={"matching_models": matching_ids})

        # 3. Safe diagnostic error message
        if "/" in model_id:
            p_id = model_id.split("/", 1)[0]
            if p_id in self._providers:
                msg = f"Model '{model_id}' was not found in registered provider '{p_id}'."
            else:
                msg = f"Model '{model_id}' was not found. Provider '{p_id}' is not registered."
        else:
            msg = f"Model '{model_id}' was not found in any registered provider."

        raise ModelNotFoundError(
            msg, details={"available_models": [m.id for m in discovery.models]}
        )

    async def resolve_provider_for_model(self, model_id: str) -> ModelProvider:
        """Resolve which registered provider owns the requested model_id.

        Resolves exact canonical IDs using registered canonical mappings.
        Does not assume an ID is canonical solely because it contains a '/'.
        Preserves slash-containing native model IDs (e.g. 'organization/model:latest').
        If a native ID matches multiple providers, raises AmbiguousModelError.

        Raises:
            AmbiguousModelError: If native model ID matches multiple providers.
            ModelNotFoundError: If no registered provider exposes this model.
        """
        # Fast path: If model_id starts with a known registered provider ID followed by '/',
        # probe that specific provider to verify exact canonical ownership without querying all.
        if "/" in model_id:
            candidate_p_id = model_id.split("/", 1)[0]
            if candidate_p_id in self._providers:
                provider = self._providers[candidate_p_id]
                try:
                    models = await provider.discover_models()
                    for m in models:
                        canon = m.as_canonical(candidate_p_id)
                        if canon.id == model_id:
                            return provider
                except Exception as e:
                    logger.debug(f"Provider '{candidate_p_id}' fast-path discovery failed: {e}")

        # Full resolution across registered models
        discovery = await self.discover_models()

        # 1. Exact match on canonical ID
        canonical_match = next((m for m in discovery.models if m.id == model_id), None)
        if canonical_match and canonical_match.provider_id:
            return self.get_provider(canonical_match.provider_id)

        # 2. Match on native model ID (both bare and slash-containing native IDs)
        native_matches = [m for m in discovery.models if m.native_model_id == model_id]
        if len(native_matches) == 1 and native_matches[0].provider_id:
            return self.get_provider(native_matches[0].provider_id)
        elif len(native_matches) > 1:
            matching_ids = [m.id for m in native_matches]
            msg = (
                f"Model identifier '{model_id}' is ambiguous across multiple providers: "
                f"{matching_ids}. Specify the canonical '<provider_id>/<model_id>' identifier."
            )
            raise AmbiguousModelError(msg, details={"matching_models": matching_ids})

        # 3. Not found: provide safe and clear diagnostics
        if "/" in model_id:
            candidate_p_id = model_id.split("/", 1)[0]
            if candidate_p_id in self._providers:
                msg = f"Model '{model_id}' was not found in registered provider '{candidate_p_id}'."
            else:
                msg = (
                    f"Model '{model_id}' was not found. "
                    f"Provider '{candidate_p_id}' is not registered."
                )
        else:
            msg = f"Cannot resolve owning provider for model '{model_id}'."

        raise ModelNotFoundError(
            msg, details={"available_models": [m.id for m in discovery.models]}
        )
