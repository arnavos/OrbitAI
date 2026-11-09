"""SQLAlchemy ORM models for conversations and messages."""

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Conversation(Base, TimestampMixin):
    """Durable conversation thread metadata with atomic sequence tracking."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="New Conversation")
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # Relationships
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.sequence",
        lazy="selectin",
    )

    __table_args__ = (Index("ix_conversations_updated_at", "updated_at"),)


class Message(Base, TimestampMixin):
    """Individual conversational turn within a conversation thread."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user, assistant, system
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="completed"
    )  # pending, streaming, completed, interrupted, failed

    # Model & generation accounting metadata
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationship back to parent conversation
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")

    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_messages_conversation_sequence"),
        Index("ix_messages_conversation_sequence", "conversation_id", "sequence"),
    )
