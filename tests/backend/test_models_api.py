"""Integration tests for AI model discovery REST endpoints."""

from collections.abc import AsyncGenerator

import pytest
from httpx import AsyncClient

from app.ai.provider import ModelProvider
from app.ai.registry import ModelRegistry
from app.ai.types import (
    ModelAvailabilityStatus,
    ModelCapability,
    ModelDescriptor,
    ProviderHealth,
    ProviderStatus,
    ProviderType,
    TokenDelta,
)
from app.main import app


class MockDiscoveryProvider(ModelProvider):
    """Mock provider with controllable model discovery behavior."""

    def __init__(self, provider_id: str = "mock-ollama", fail_discovery: bool = False) -> None:
        self._provider_id = provider_id
        self._fail = fail_discovery

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.OLLAMA

    async def check_health(self) -> ProviderHealth:
        return ProviderHealth(provider_id=self._provider_id, status=ProviderStatus.ONLINE)

    async def discover_models(self) -> list[ModelDescriptor]:
        if self._fail:
            raise RuntimeError(f"Daemon socket connection timeout to {self._provider_id}")
        return [
            ModelDescriptor(
                id=f"{self._provider_id}/llama3.2:3b",
                name="Llama 3.2 3B",
                provider_type=self.provider_type,
                model_family="llama",
                parameter_size="3.2B",
                quantization="Q4_K_M",
                context_window_capacity=131072,
                estimated_memory_bytes=2147483648,
                capabilities={ModelCapability.STREAMING, ModelCapability.CANCELLATION},
                availability_status=ModelAvailabilityStatus.AVAILABLE,
                native_model_id="llama3.2:3b",
                provider_id=self._provider_id,
            ),
            ModelDescriptor(
                id=f"{self._provider_id}/qwen2.5:7b",
                name="Qwen 2.5 7B",
                provider_type=self.provider_type,
                model_family="qwen",
                parameter_size="7B",
                quantization="Q4_K_M",
                context_window_capacity=32768,
                estimated_memory_bytes=4294967296,
                capabilities={ModelCapability.STREAMING},
                availability_status=ModelAvailabilityStatus.LOADED,
                native_model_id="qwen2.5:7b",
                provider_id=self._provider_id,
            ),
        ]

    @property
    def display_name(self) -> str:
        return f"Mock Provider ({self._provider_id})"

    async def stream_inference(self, *args, **kwargs) -> AsyncGenerator[TokenDelta]:
        yield TokenDelta(text="test")

    async def cancel_inference(self, *args, **kwargs) -> bool:
        return True

    async def unload_model(self, *args, **kwargs) -> bool:
        return True

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_list_models_unauthorized_rejected(async_client: AsyncClient):
    """Verify unauthenticated requests to /api/v1/models are rejected with HTTP 401."""
    res = await async_client.get("/api/v1/models")
    assert res.status_code == 401
    data = res.json()
    assert data["success"] is False


@pytest.mark.asyncio
async def test_list_models_empty_registry(async_client: AsyncClient, auth_headers: dict[str, str]):
    """Verify empty registry returns 200 with empty model inventory and zero errors."""
    orig_registry = getattr(app.state, "model_registry", None)
    app.state.model_registry = ModelRegistry()

    try:
        res = await async_client.get("/api/v1/models", headers=auth_headers)
        assert res.status_code == 200
        payload = res.json()
        assert payload["success"] is True
        data = payload["data"]
        assert data["models"] == []
        assert data["errors"] == {}
        assert "discovered_at" in data
    finally:
        app.state.model_registry = orig_registry


@pytest.mark.asyncio
async def test_list_models_successful_discovery(
    async_client: AsyncClient, auth_headers: dict[str, str]
):
    """Verify model discovery returns canonical descriptors with rich metadata."""
    orig_registry = getattr(app.state, "model_registry", None)
    registry = ModelRegistry()
    mock_p = MockDiscoveryProvider(provider_id="local-ollama")
    await registry.register_provider(mock_p)
    app.state.model_registry = registry

    try:
        res = await async_client.get("/api/v1/models", headers=auth_headers)
        assert res.status_code == 200
        payload = res.json()
        assert payload["success"] is True
        data = payload["data"]
        assert len(data["models"]) == 2
        assert data["errors"] == {}

        llama = next(m for m in data["models"] if m["id"] == "local-ollama/llama3.2:3b")
        assert llama["name"] == "Llama 3.2 3B"
        assert llama["parameter_size"] == "3.2B"
        assert llama["quantization"] == "Q4_K_M"
        assert llama["context_window_capacity"] == 131072
        assert llama["estimated_memory_bytes"] == 2147483648
        assert llama["availability_status"] == "available"
        assert "streaming" in llama["capabilities"]

        qwen = next(m for m in data["models"] if m["id"] == "local-ollama/qwen2.5:7b")
        assert qwen["availability_status"] == "loaded"
        assert qwen["parameter_size"] == "7B"
    finally:
        app.state.model_registry = orig_registry


@pytest.mark.asyncio
async def test_list_models_provider_failure_isolation(
    async_client: AsyncClient, auth_headers: dict[str, str]
):
    """Verify provider discovery failure is isolated and does not crash the endpoint."""
    orig_registry = getattr(app.state, "model_registry", None)
    registry = ModelRegistry()
    healthy = MockDiscoveryProvider(provider_id="healthy-provider")
    failing = MockDiscoveryProvider(provider_id="offline-provider", fail_discovery=True)
    await registry.register_provider(healthy)
    await registry.register_provider(failing)
    app.state.model_registry = registry

    try:
        res = await async_client.get("/api/v1/models", headers=auth_headers)
        assert res.status_code == 200
        payload = res.json()
        assert payload["success"] is True
        data = payload["data"]
        # Healthy models returned
        assert len(data["models"]) == 2
        # Failing provider error recorded
        assert "offline-provider" in data["errors"]
        assert "timeout" in data["errors"]["offline-provider"].lower()
    finally:
        app.state.model_registry = orig_registry


@pytest.mark.asyncio
async def test_list_models_no_sensitive_credentials_exposed(
    async_client: AsyncClient, auth_headers: dict[str, str]
):
    """Verify discovery payload strictly conceals internal connection secrets."""
    res = await async_client.get("/api/v1/models", headers=auth_headers)
    assert res.status_code == 200
    text_content = res.text.lower()
    assert "password" not in text_content
    assert "secret" not in text_content
    assert "token" not in text_content or "x-orbit-session-token" in text_content
