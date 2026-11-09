"""Pydantic schemas for conversation management and synchronous messaging."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    """Payload to initialize a new conversation thread."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=255, description="Thread display title")
    model_id: str | None = Field(default=None, max_length=128, description="Default model ID")


class ConversationUpdate(BaseModel):
    """Payload to modify conversation thread metadata."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=255, description="Updated display title")
    model_id: str | None = Field(default=None, max_length=128, description="Updated default model")


class ConversationResponse(BaseModel):
    """Normalized response representation of a conversation thread."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    model_id: str | None = None
    last_sequence: int = 0
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ConversationListResponse(BaseModel):
    """Paginated collection of conversation threads."""

    conversations: list[ConversationResponse]
    total: int


class MessageResponse(BaseModel):
    """Normalized response representation of a single conversational turn."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    sequence: int
    role: str
    content: str
    status: str
    model_id: str | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class MessageListResponse(BaseModel):
    """Ordered sequence of message turns within a conversation."""

    messages: list[MessageResponse]
    total: int


class SendMessageRequest(BaseModel):
    """Payload for submitting a user prompt to a conversation thread."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, description="User prompt text")
    model_id: str | None = Field(default=None, description="Model to execute this turn")
    temperature: float | None = Field(
        default=None, ge=0.0, le=2.0, description="Sampling randomness"
    )
    max_output_tokens: int | None = Field(
        default=None, gt=0, description="Requested output token bound"
    )
    system_prompt: str | None = Field(
        default=None, description="Optional custom system prompt for this turn"
    )


class SendMessageResponse(BaseModel):
    """Atomic response containing the persisted user message and generated assistant turn."""

    user_message: MessageResponse
    assistant_message: MessageResponse
