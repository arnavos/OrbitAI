"""Unit tests for the ModelRegistry multi-provider discovery, lookup, and failure isolation."""

import asyncio

import pytest
from tests.backend.test_ai_provider_contract import MockTestProvider

from app.ai.exceptions import (
    AmbiguousModelError,
    DuplicateProviderError,
    ModelNotFoundError,
    ProviderNotFoundError,
)
from app.ai.registry import ModelRegistry
from app.ai.types import ModelCapability, ModelDescriptor, ProviderStatus, ProviderType


@pytest.fixture
def registry() -> ModelRegistry:
    """Fixture providing a fresh in-memory ModelRegistry instance."""
    return ModelRegistry()


# ==============================================================================
# Registration & Lookup Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_register_and_lookup_provider(registry: ModelRegistry):
    """Verify provider registration and lookup by ID."""
    p1 = MockTestProvider(provider_id="mock-ollama")
    await registry.register_provider(p1)

    assert registry.get_provider("mock-ollama") is p1
    assert len(registry.list_providers()) == 1


@pytest.mark.asyncio
async def test_duplicate_provider_registration_rejected(registry: ModelRegistry):
    """Verify registering a duplicate provider_id raises DuplicateProviderError."""
    p1 = MockTestProvider(provider_id="mock-ollama")
    p2 = MockTestProvider(provider_id="mock-ollama")

    await registry.register_provider(p1)
    with pytest.raises(DuplicateProviderError, match="already registered"):
        await registry.register_provider(p2)


@pytest.mark.asyncio
async def test_lookup_unregistered_provider_raises_not_found(registry: ModelRegistry):
    """Verify lookup of unknown provider raises ProviderNotFoundError."""
    with pytest.raises(ProviderNotFoundError, match="is not registered"):
        registry.get_provider("nonexistent-provider")


@pytest.mark.asyncio
async def test_unregister_provider(registry: ModelRegistry):
    """Verify unregistering a provider removes it from active registry."""
    p1 = MockTestProvider(provider_id="p-remove")
    await registry.register_provider(p1)
    assert len(registry.list_providers()) == 1

    removed = await registry.unregister_provider("p-remove")
    assert removed is True
    assert len(registry.list_providers()) == 0

    # Unregistering nonexistent returns False
    removed_again = await registry.unregister_provider("p-remove")
    assert removed_again is False


# ==============================================================================
# Concurrency Tests (asyncio.gather)
# ==============================================================================


@pytest.mark.asyncio
async def test_concurrent_provider_registration(registry: ModelRegistry):
    """Verify concurrent registration of distinct providers maintains consistent state."""
    providers = [MockTestProvider(provider_id=f"concurrent-p{i}") for i in range(10)]
    tasks = [registry.register_provider(p) for p in providers]
    await asyncio.gather(*tasks)

    assert len(registry.list_providers()) == 10
    for i in range(10):
        assert registry.get_provider(f"concurrent-p{i}") is providers[i]


@pytest.mark.asyncio
async def test_concurrent_duplicate_provider_registration(registry: ModelRegistry):
    """Verify concurrent registration attempts for the same ID allow exactly one winner."""
    providers = [MockTestProvider(provider_id="same-id") for _ in range(5)]
    results = await asyncio.gather(
        *[registry.register_provider(p) for p in providers], return_exceptions=True
    )

    successes = [r for r in results if r is None]
    duplicates = [r for r in results if isinstance(r, DuplicateProviderError)]

    assert len(successes) == 1
    assert len(duplicates) == 4
    assert len(registry.list_providers()) == 1


@pytest.mark.asyncio
async def test_concurrent_provider_unregistration(registry: ModelRegistry):
    """Verify concurrent unregistration of registered and non-registered providers."""
    for i in range(5):
        await registry.register_provider(MockTestProvider(provider_id=f"unreg-{i}"))

    tasks = [registry.unregister_provider(f"unreg-{i}") for i in range(5)]
    results = await asyncio.gather(*tasks)

    assert all(r is True for r in results)
    assert len(registry.list_providers()) == 0


# ==============================================================================
# Health Probing Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_check_all_health(registry: ModelRegistry):
    """Verify concurrent health check across multiple providers."""
    p_healthy = MockTestProvider(provider_id="p-healthy", should_fail_health=False)
    p_offline = MockTestProvider(provider_id="p-offline", should_fail_health=True)

    await registry.register_provider(p_healthy)
    await registry.register_provider(p_offline)

    health_map = await registry.check_all_health()
    assert len(health_map) == 2
    assert health_map["p-healthy"].status == ProviderStatus.ONLINE
    assert health_map["p-offline"].status == ProviderStatus.OFFLINE


@pytest.mark.asyncio
async def test_check_all_health_empty_registry(registry: ModelRegistry):
    """Verify health check on empty registry returns empty mapping."""
    health_map = await registry.check_all_health()
    assert health_map == {}


# ==============================================================================
# Model Discovery & Failure Isolation Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_discover_models_across_multiple_providers(registry: ModelRegistry):
    """Verify parallel discovery discovers models from all healthy providers."""
    p1 = MockTestProvider(provider_id="p1")
    p2 = MockTestProvider(provider_id="p2")

    await registry.register_provider(p1)
    await registry.register_provider(p2)

    result = await registry.discover_models()
    assert len(result.models) == 4  # 2 models per provider
    assert len(result.errors) == 0

    model_ids = [m.id for m in result.models]
    assert "p1/mock-model-1" in model_ids
    assert "p2/mock-model-1" in model_ids


@pytest.mark.asyncio
async def test_discover_models_failure_isolation(registry: ModelRegistry):
    """Verify that a failing provider does not block discovery of healthy providers."""
    p_healthy = MockTestProvider(provider_id="p-healthy", should_fail_discovery=False)
    p_failing = MockTestProvider(provider_id="p-failing", should_fail_discovery=True)

    await registry.register_provider(p_healthy)
    await registry.register_provider(p_failing)

    result = await registry.discover_models()

    # Healthy models are preserved!
    assert len(result.models) == 2
    assert result.models[0].id.startswith("p-healthy/")

    # Failing provider is isolated and recorded in errors
    assert "p-failing" in result.errors
    assert "connection refused" in result.errors["p-failing"].lower()


@pytest.mark.asyncio
async def test_discover_models_duplicate_native_model_ids_preserved(registry: ModelRegistry):
    """Verify identical native model IDs across distinct providers are preserved under canonical IDs."""

    class DuplicateNativeProvider(MockTestProvider):
        async def discover_models(self) -> list[ModelDescriptor]:
            return [
                ModelDescriptor(
                    id="llama3.2:3b",
                    name="Llama 3.2 3B",
                    provider_type=ProviderType.MOCK,
                    context_window_capacity=8192,
                )
            ]

    p1 = DuplicateNativeProvider(provider_id="ollama-local")
    p2 = DuplicateNativeProvider(provider_id="lmstudio-local")

    await registry.register_provider(p1)
    await registry.register_provider(p2)

    result = await registry.discover_models()

    # Both models are preserved! Neither is dropped due to discovery timing
    assert len(result.models) == 2
    canonical_ids = {m.id for m in result.models}
    assert "ollama-local/llama3.2:3b" in canonical_ids
    assert "lmstudio-local/llama3.2:3b" in canonical_ids

    # Both models preserve their native model ID
    for m in result.models:
        assert m.native_model_id == "llama3.2:3b"


@pytest.mark.asyncio
async def test_discover_models_duplicate_id_within_same_provider_ignored(registry: ModelRegistry):
    """Verify duplicate model ID within the SAME provider is de-duplicated."""

    class SameProviderDuplicates(MockTestProvider):
        async def discover_models(self) -> list[ModelDescriptor]:
            return [
                ModelDescriptor(
                    id="same-model",
                    name="Model V1",
                    provider_type=ProviderType.MOCK,
                    context_window_capacity=4096,
                ),
                ModelDescriptor(
                    id="same-model",
                    name="Model V2 Duplicate",
                    provider_type=ProviderType.MOCK,
                    context_window_capacity=4096,
                ),
            ]

    p = SameProviderDuplicates(provider_id="single-provider")
    await registry.register_provider(p)

    result = await registry.discover_models()
    assert len(result.models) == 1
    assert result.models[0].id == "single-provider/same-model"


@pytest.mark.asyncio
async def test_discover_models_empty_registry(registry: ModelRegistry):
    """Verify discovering models with no providers returns clean empty result."""
    result = await registry.discover_models()
    assert result.models == []
    assert result.errors == {}


# ==============================================================================
# Model Resolution Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_get_model_canonical_success(registry: ModelRegistry):
    """Verify get_model locates and returns descriptor via canonical ID."""
    p1 = MockTestProvider(provider_id="p1")
    await registry.register_provider(p1)

    model = await registry.get_model("p1/mock-model-2")
    assert model.id == "p1/mock-model-2"
    assert model.native_model_id == "mock-model-2"
    assert model.context_window_capacity == 8192
    assert model.has_capability(ModelCapability.CANCELLATION) is True


@pytest.mark.asyncio
async def test_get_model_unambiguous_bare_id_success(registry: ModelRegistry):
    """Verify get_model resolves unique bare native ID when only one provider offers it."""
    p1 = MockTestProvider(provider_id="p1")
    await registry.register_provider(p1)

    model = await registry.get_model("mock-model-2")
    assert model.id == "p1/mock-model-2"
    assert model.native_model_id == "mock-model-2"


@pytest.mark.asyncio
async def test_get_model_ambiguous_bare_id_raises(registry: ModelRegistry):
    """Verify get_model with bare ID matching multiple providers raises AmbiguousModelError."""

    class SharedModelProvider(MockTestProvider):
        async def discover_models(self) -> list[ModelDescriptor]:
            return [
                ModelDescriptor(
                    id="shared-native-id",
                    name="Shared Model",
                    provider_type=ProviderType.MOCK,
                    context_window_capacity=4096,
                )
            ]

    p1 = SharedModelProvider(provider_id="provider-alpha")
    p2 = SharedModelProvider(provider_id="provider-beta")

    await registry.register_provider(p1)
    await registry.register_provider(p2)

    with pytest.raises(AmbiguousModelError, match="is ambiguous across multiple providers"):
        await registry.get_model("shared-native-id")

    # But canonical IDs succeed without ambiguity!
    m1 = await registry.get_model("provider-alpha/shared-native-id")
    assert m1.id == "provider-alpha/shared-native-id"

    m2 = await registry.get_model("provider-beta/shared-native-id")
    assert m2.id == "provider-beta/shared-native-id"


@pytest.mark.asyncio
async def test_get_model_not_found_raises(registry: ModelRegistry):
    """Verify get_model with unknown ID raises ModelNotFoundError with diagnostic details."""
    p1 = MockTestProvider(provider_id="p1")
    await registry.register_provider(p1)

    with pytest.raises(
        ModelNotFoundError, match="was not found in any registered provider"
    ) as exc_info:
        await registry.get_model("unknown-model-xyz")

    assert "p1/mock-model-1" in exc_info.value.details.get("available_models", [])


@pytest.mark.asyncio
async def test_resolve_provider_canonical_fast_path(registry: ModelRegistry):
    """Verify resolve_provider_for_model uses canonical ID fast path."""
    p1 = MockTestProvider(provider_id="p1")
    p2 = MockTestProvider(provider_id="p2")

    await registry.register_provider(p1)
    await registry.register_provider(p2)

    owner1 = await registry.resolve_provider_for_model("p1/mock-model-1")
    assert owner1.provider_id == "p1"

    owner2 = await registry.resolve_provider_for_model("p2/mock-model-2")
    assert owner2.provider_id == "p2"


@pytest.mark.asyncio
async def test_resolve_provider_ambiguous_bare_id_raises(registry: ModelRegistry):
    """Verify resolve_provider_for_model with ambiguous bare ID raises AmbiguousModelError."""

    class AmbiguousProvider(MockTestProvider):
        async def discover_models(self) -> list[ModelDescriptor]:
            return [
                ModelDescriptor(
                    id="ambiguous-native",
                    name="Ambiguous",
                    provider_type=ProviderType.MOCK,
                    context_window_capacity=4096,
                )
            ]

    p1 = AmbiguousProvider(provider_id="prov-1")
    p2 = AmbiguousProvider(provider_id="prov-2")

    await registry.register_provider(p1)
    await registry.register_provider(p2)

    with pytest.raises(AmbiguousModelError, match="is ambiguous across multiple providers"):
        await registry.resolve_provider_for_model("ambiguous-native")

    # Canonical resolutions succeed
    assert (
        await registry.resolve_provider_for_model("prov-1/ambiguous-native")
    ).provider_id == "prov-1"
    assert (
        await registry.resolve_provider_for_model("prov-2/ambiguous-native")
    ).provider_id == "prov-2"


@pytest.mark.asyncio
async def test_resolve_provider_unknown_model_raises(registry: ModelRegistry):
    """Verify resolve_provider_for_model with nonexistent model raises ModelNotFoundError."""
    with pytest.raises(ModelNotFoundError, match="Cannot resolve owning provider"):
        await registry.resolve_provider_for_model("nonexistent-model")


@pytest.mark.asyncio
async def test_slash_containing_native_model_resolution(registry: ModelRegistry):
    """Verify slash-containing native model IDs (e.g. meta-llama/Llama-3.2-3B) resolve correctly."""

    class SlashNativeProvider(MockTestProvider):
        async def discover_models(self) -> list[ModelDescriptor]:
            return [
                ModelDescriptor(
                    id="meta-llama/Llama-3.2-3B",
                    name="Llama 3.2 3B Instruct",
                    provider_type=ProviderType.OLLAMA,
                    context_window_capacity=8192,
                )
            ]

    provider = SlashNativeProvider(provider_id="ollama")
    await registry.register_provider(provider)

    # 1. Exact canonical ID lookup
    owner_canon = await registry.resolve_provider_for_model("ollama/meta-llama/Llama-3.2-3B")
    assert owner_canon.provider_id == "ollama"

    model_canon = await registry.get_model("ollama/meta-llama/Llama-3.2-3B")
    assert model_canon.id == "ollama/meta-llama/Llama-3.2-3B"
    assert model_canon.native_model_id == "meta-llama/Llama-3.2-3B"

    # 2. Native ID lookup without provider prefix (unambiguous)
    owner_native = await registry.resolve_provider_for_model("meta-llama/Llama-3.2-3B")
    assert owner_native.provider_id == "ollama"

    model_native = await registry.get_model("meta-llama/Llama-3.2-3B")
    assert model_native.id == "ollama/meta-llama/Llama-3.2-3B"
    assert model_native.native_model_id == "meta-llama/Llama-3.2-3B"


@pytest.mark.asyncio
async def test_multiple_providers_same_slash_containing_native_id(registry: ModelRegistry):
    """Verify multiple providers offering identical slash-containing native IDs are handled safely."""

    class SlashProvider(MockTestProvider):
        async def discover_models(self) -> list[ModelDescriptor]:
            return [
                ModelDescriptor(
                    id="organization/model-x:v1",
                    name="Org Model X",
                    provider_type=ProviderType.MOCK,
                    context_window_capacity=4096,
                )
            ]

    p1 = SlashProvider(provider_id="local-ollama")
    p2 = SlashProvider(provider_id="local-llamacpp")

    await registry.register_provider(p1)
    await registry.register_provider(p2)

    # Bare query across multiple providers must raise AmbiguousModelError
    with pytest.raises(AmbiguousModelError, match="is ambiguous across multiple providers"):
        await registry.resolve_provider_for_model("organization/model-x:v1")

    with pytest.raises(AmbiguousModelError, match="is ambiguous across multiple providers"):
        await registry.get_model("organization/model-x:v1")

    # Exact canonical resolution succeeds unambiguously
    p1_owner = await registry.resolve_provider_for_model("local-ollama/organization/model-x:v1")
    assert p1_owner.provider_id == "local-ollama"

    p2_owner = await registry.resolve_provider_for_model("local-llamacpp/organization/model-x:v1")
    assert p2_owner.provider_id == "local-llamacpp"

    p1_model = await registry.get_model("local-ollama/organization/model-x:v1")
    assert p1_model.id == "local-ollama/organization/model-x:v1"
    assert p1_model.native_model_id == "organization/model-x:v1"

    p2_model = await registry.get_model("local-llamacpp/organization/model-x:v1")
    assert p2_model.id == "local-llamacpp/organization/model-x:v1"
    assert p2_model.native_model_id == "organization/model-x:v1"


@pytest.mark.asyncio
async def test_unknown_provider_prefix_raises(registry: ModelRegistry):
    """Verify querying an unknown provider prefix safely raises ModelNotFoundError with diagnostic."""
    p1 = MockTestProvider(provider_id="p1")
    await registry.register_provider(p1)

    with pytest.raises(ModelNotFoundError, match="Provider 'unknown-provider' is not registered"):
        await registry.resolve_provider_for_model("unknown-provider/some-model")

    with pytest.raises(ModelNotFoundError, match="Provider 'unknown-provider' is not registered"):
        await registry.get_model("unknown-provider/some-model")


@pytest.mark.asyncio
async def test_known_provider_nonexistent_model_raises(registry: ModelRegistry):
    """Verify querying a nonexistent model on a registered provider raises ModelNotFoundError."""
    p1 = MockTestProvider(provider_id="p1")
    await registry.register_provider(p1)

    with pytest.raises(ModelNotFoundError, match="was not found in registered provider 'p1'"):
        await registry.resolve_provider_for_model("p1/nonexistent-model")

    with pytest.raises(ModelNotFoundError, match="was not found in registered provider 'p1'"):
        await registry.get_model("p1/nonexistent-model")
