"""Comprehensive tests for FastAPI lifespan lifecycle and AI provider subsystem wiring."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.ai.provider import ModelProvider
from app.ai.registry import ModelRegistry
from app.ai.types import ProviderHealth, ProviderStatus
from app.main import create_application


def test_fastapi_lifespan_starts_and_stops_cleanly_online():
    """Verify application starts and stops cleanly when Ollama is online."""
    app = create_application()

    mock_health = ProviderHealth(
        provider_id="ollama",
        status=ProviderStatus.ONLINE,
        latency_ms=12.5,
        message="Ollama 0.40.14",
    )

    with (
        patch(
            "app.ai.providers.ollama.OllamaProvider.check_health",
            new_callable=AsyncMock,
            return_value=mock_health,
        ),
        patch(
            "app.ai.providers.ollama.OllamaProvider.close",
            new_callable=AsyncMock,
        ) as mock_close,
        TestClient(app) as client,
    ):
        # App is running
        resp = client.get("/health")
        assert resp.status_code == 200

        # Verify registry and provider are stored on app.state
        assert hasattr(app.state, "model_registry")
        assert isinstance(app.state.model_registry, ModelRegistry)
        assert hasattr(app.state, "ollama_provider")
        assert isinstance(app.state.ollama_provider, ModelProvider)

        # Provider registered exactly once
        providers = app.state.model_registry.list_providers()
        assert len(providers) == 1
        assert providers[0].provider_id == "ollama"

    # Verify shutdown closes HTTP client
    mock_close.assert_awaited_once()


def test_fastapi_lifespan_starts_when_ollama_is_offline():
    """Verify application starts normally even when Ollama daemon is offline."""
    app = create_application()

    offline_health = ProviderHealth(
        provider_id="ollama",
        status=ProviderStatus.OFFLINE,
        latency_ms=None,
        message="Ollama connection refused",
    )

    with (
        patch(
            "app.ai.providers.ollama.OllamaProvider.check_health",
            new_callable=AsyncMock,
            return_value=offline_health,
        ),
        patch(
            "app.ai.providers.ollama.OllamaProvider.close",
            new_callable=AsyncMock,
        ) as mock_close,
        TestClient(app) as client,
    ):
        # App is functional despite Ollama being offline
        resp = client.get("/health")
        assert resp.status_code == 200
        assert hasattr(app.state, "model_registry")

    mock_close.assert_awaited_once()


def test_fastapi_lifespan_repeated_start_stop():
    """Verify repeated startup and shutdown cycles execute without resource leaks."""
    app = create_application()

    for _ in range(3):
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            assert hasattr(app.state, "model_registry")


def test_fastapi_lifespan_startup_failure_cleanup():
    """Verify that if startup encounters an error during provider registration, resources are cleaned up."""
    app = create_application()

    with (
        patch(
            "app.ai.registry.ModelRegistry.register_provider",
            side_effect=RuntimeError("Simulated registration failure"),
        ),
        patch(
            "app.ai.providers.ollama.OllamaProvider.close",
            new_callable=AsyncMock,
        ) as mock_close,
    ):
        with pytest.raises(RuntimeError, match="Simulated registration failure"):
            with TestClient(app):
                pass

        mock_close.assert_awaited_once()
