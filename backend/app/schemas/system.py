"""Pydantic schemas for health, system telemetry, and configuration settings."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Basic health probe response."""

    status: str = Field(
        ..., description="Overall health status: 'healthy', 'degraded', or 'unhealthy'"
    )
    version: str = Field(..., description="Application version")
    uptime_seconds: float = Field(..., description="Elapsed uptime of the backend process")


class DetailedHealthResponse(HealthResponse):
    """Detailed health telemetry with resource consumption and database diagnostics."""

    pid: int = Field(..., description="Operating system Process ID of the backend daemon")
    memory_rss_mb: float = Field(..., description="Resident Set Size memory in MB")
    cpu_percent: float = Field(..., description="Current process CPU utilization percentage")
    database_connected: bool = Field(..., description="Database connectivity status")
    database_latency_ms: float = Field(..., description="Latency of SQLite roundtrip probe in ms")
    journal_mode: str = Field(..., description="Current SQLite journal mode (e.g. WAL)")


class SystemInfoResponse(BaseModel):
    """Host operating system, architecture, and runtime environment specifications."""

    app_name: str
    app_version: str
    environment: str
    platform: str
    architecture: str
    python_version: str
    sqlite_version: str
    total_memory_mb: float
    available_memory_mb: float


class SystemSettingCreate(BaseModel):
    """Payload to create or update a system setting."""

    key: str = Field(..., max_length=128, description="Setting configuration key")
    value: Any = Field(..., description="Arbitrary JSON-serializable value")
    is_encrypted: bool = Field(
        default=False, description="Whether value requires encryption at rest"
    )


class SystemSettingResponse(BaseModel):
    """Retrieved system setting."""

    key: str
    value: Any
    is_encrypted: bool
    updated_at: datetime


class AuditEventResponse(BaseModel):
    """Audit event record."""

    id: int
    correlation_id: str
    event_type: str
    actor: str
    severity: str
    payload: dict[str, Any]
    created_at: datetime
