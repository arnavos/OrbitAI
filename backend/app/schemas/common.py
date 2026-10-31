"""Standardized response envelope schemas for all ORBIT API endpoints."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Normalized error detail payload."""

    code: str = Field(..., description="Machine-readable error identifier")
    message: str = Field(..., description="Human-readable explanation of the error")
    details: dict[str, Any] | None = Field(
        default_factory=dict, description="Additional context or validation errors"
    )


class APIResponse[T](BaseModel):
    """Standard unified envelope wrapping all API responses."""

    success: bool = Field(..., description="True if operation succeeded, false otherwise")
    data: T | None = Field(default=None, description="Payload data returned on success")
    error: ErrorDetail | None = Field(default=None, description="Error information on failure")
    correlation_id: str = Field(..., description="Unique request tracing ID")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 UTC timestamp of response generation",
    )
