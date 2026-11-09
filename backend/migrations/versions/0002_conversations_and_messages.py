"""conversations and messages schema with atomic sequence counter

Revision ID: 0002_conversations_and_messages
Revises: 0001_initial_schema
Create Date: 2026-11-09 12:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_conversations_and_messages"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Conversations Table
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "title",
            sa.String(length=255),
            nullable=False,
            server_default="New Conversation",
        ),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.Column(
            "last_sequence",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversations_updated_at", "conversations", ["updated_at"])

    # 2. Messages Table
    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(length=36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="completed",
        ),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.Column("finish_reason", sa.String(length=16), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_messages_conversation_sequence"
        ),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index(
        "ix_messages_conversation_sequence",
        "messages",
        ["conversation_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_messages_conversation_sequence", table_name="messages")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_updated_at", table_name="conversations")
    op.drop_table("conversations")
