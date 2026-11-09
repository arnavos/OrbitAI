"""Pydantic schemas and serialization protocols for SSE token streaming."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

STREAM_PROTOCOL_VERSION = "1.0"


class StreamEventType(StrEnum):
    """Supported streaming lifecycle event types."""

    STREAM_STARTED = "stream.started"
    MESSAGE_DELTA = "message.delta"
    STREAM_COMPLETED = "stream.completed"
    STREAM_FAILED = "stream.failed"
    STREAM_INTERRUPTED = "stream.interrupted"
    STREAM_CANCELLED = "stream.cancelled"


class StreamUserMessageSnapshot(BaseModel):
    """Snapshot of user turn metadata emitted at stream initialization."""

    model_config = ConfigDict(extra="forbid")

    id: str
    conversation_id: str
    sequence: int
    role: str = "user"
    content: str
    status: str = "completed"
    created_at: datetime


class StreamStartedPayload(BaseModel):
    """Payload emitted when conversation turn starts generation."""

    model_config = ConfigDict(extra="forbid")

    user_message: StreamUserMessageSnapshot
    assistant_message_id: str
    assistant_sequence: int
    model_id: str
    allocated_context: int
    created_at: datetime


class MessageDeltaPayload(BaseModel):
    """Payload emitted for each incremental token delta."""

    model_config = ConfigDict(extra="forbid")

    text: str
    token_index: int = Field(..., ge=0)
    accumulated_tokens: int = Field(..., ge=1)


class StreamCompletedPayload(BaseModel):
    """Terminal payload emitted on successful generation completion."""

    model_config = ConfigDict(extra="forbid")

    finish_reason: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    duration_ms: float = Field(default=0.0, ge=0.0)


class StreamInterruptedPayload(BaseModel):
    """Terminal payload emitted when generation is interrupted by transport/system failure."""

    model_config = ConfigDict(extra="forbid")

    reason: str
    partial_tokens_count: int = Field(default=0, ge=0)
    partial_text_length: int = Field(default=0, ge=0)


class StreamCancelledPayload(BaseModel):
    """Terminal payload emitted when generation is cancelled explicitly by user or request."""

    model_config = ConfigDict(extra="forbid")

    reason: str = "cancelled_by_user"
    partial_tokens_count: int = Field(default=0, ge=0)
    partial_text_length: int = Field(default=0, ge=0)


class StreamFailedPayload(BaseModel):
    """Terminal payload emitted when an unrecoverable inference or provider error occurs."""

    model_config = ConfigDict(extra="forbid")

    error_code: str
    error_message: str
    details: dict[str, Any] | None = None


class StreamEventEnvelope[PayloadT: BaseModel](BaseModel):
    """Standardized envelope wrapping all streaming protocol events."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: str = Field(default=STREAM_PROTOCOL_VERSION)
    event_type: StreamEventType
    conversation_id: str
    message_id: str
    event_seq: int = Field(..., ge=0, description="Monotonically increasing sequence within stream")
    payload: PayloadT

    @property
    def data(self) -> PayloadT:
        """Alias to access payload directly."""
        return self.payload

    def format_sse(self) -> str:
        """Format as a standard W3C Server-Sent Event frame."""
        return f"event: {self.event_type.value}\ndata: {self.model_dump_json()}\n\n"


def format_ping() -> str:
    """Format an SSE comment keep-alive heartbeat."""
    return ": ping\n\n"
