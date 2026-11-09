"""Pydantic schemas for AI model discovery and capabilities metadata."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.ai.types import ModelDescriptor


class ModelDescriptorResponse(BaseModel):
    """Normalized metadata describing an available or resident local AI model."""

    id: str = Field(
        ..., description="Canonical provider-qualified model ID (e.g. 'ollama/llama3.2:3b')"
    )
    name: str = Field(..., description="Human-readable model label")
    provider_type: str = Field(..., description="Backend provider classification (e.g. 'ollama')")
    model_family: str | None = Field(
        default=None, description="Model architecture family (e.g. 'llama')"
    )
    parameter_size: str | None = Field(default=None, description="Parameter scale (e.g. '3.2B')")
    quantization: str | None = Field(
        default=None, description="Quantization format (e.g. 'Q4_K_M')"
    )
    context_window_capacity: int | None = Field(
        default=None, description="Maximum context window capacity in tokens"
    )
    estimated_memory_bytes: int | None = Field(
        default=None, description="Estimated unified memory footprint in bytes"
    )
    availability_status: str = Field(
        default="available", description="Operational readiness ('available', 'loaded', etc.)"
    )
    provider_id: str | None = Field(default=None, description="Identifier of the hosting provider")
    native_model_id: str | None = Field(
        default=None, description="Native identifier within the provider (e.g. 'llama3.2:3b')"
    )
    capabilities: list[str] = Field(
        default_factory=list, description="List of supported model capabilities"
    )

    @classmethod
    def from_descriptor(cls, descriptor: ModelDescriptor) -> "ModelDescriptorResponse":
        """Convert internal ModelDescriptor domain object to API response schema."""
        return cls(
            id=descriptor.id,
            name=descriptor.name,
            provider_type=str(descriptor.provider_type),
            model_family=descriptor.model_family,
            parameter_size=descriptor.parameter_size,
            quantization=descriptor.quantization,
            context_window_capacity=descriptor.context_window_capacity,
            estimated_memory_bytes=descriptor.estimated_memory_bytes,
            availability_status=str(descriptor.availability_status),
            provider_id=descriptor.provider_id,
            native_model_id=descriptor.native_model_id,
            capabilities=[str(c) for c in descriptor.capabilities],
        )


class ModelDiscoveryResponse(BaseModel):
    """Aggregated result of cross-provider model discovery."""

    models: list[ModelDescriptorResponse] = Field(
        default_factory=list, description="List of successfully discovered local models"
    )
    errors: dict[str, str] = Field(
        default_factory=dict, description="Error diagnostics for providers that failed discovery"
    )
    discovered_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="ISO 8601 UTC timestamp when discovery was executed",
    )
