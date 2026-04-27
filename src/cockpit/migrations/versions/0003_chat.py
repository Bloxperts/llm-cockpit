"""UC-04/UC-05 chat + code tables: conversations + messages

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-28

Adds the two tables UC-04 (chat) and UC-05 (code) write:

- `conversations` — one row per chat-or-code session. The `mode` column
  discriminates ('chat' | 'code'). UC-05 reuses this table; the picker
  filters by `mode` per-page.
- `messages` — one row per turn (user / assistant / system).
  `usage_in`/`usage_out`/`gen_tps`/`latency_ms` are extracted from
  Ollama's final NDJSON chunk; `error='stream_aborted'` on partial saves.

Indexes prioritise the dashboard `last_calls` query (UC-02) and the
conversation list (UC-04 / UC-05 left rail).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("mode", sa.String, nullable=False, server_default="chat"),
        sa.Column("model", sa.String, nullable=True),
        sa.Column("title", sa.String, nullable=True),
        sa.Column("system_prompt", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint("mode IN ('chat', 'code')", name="ck_conversations_mode"),
    )
    op.create_index(
        "idx_conversations_user_mode", "conversations", ["user_id", "mode"]
    )
    op.create_index(
        "idx_conversations_user_updated",
        "conversations",
        ["user_id", "updated_at"],
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer,
            sa.ForeignKey("conversations.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("model", sa.String, nullable=True),
        sa.Column("usage_in", sa.Integer, nullable=True),
        sa.Column("usage_out", sa.Integer, nullable=True),
        sa.Column("gen_tps", sa.Float, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column(
            "ts",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("error", sa.String, nullable=True),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')", name="ck_messages_role"
        ),
    )
    op.create_index(
        "idx_messages_conversation_ts", "messages", ["conversation_id", "ts"]
    )


def downgrade() -> None:
    op.drop_index("idx_messages_conversation_ts", table_name="messages")
    op.drop_table("messages")
    op.drop_index("idx_conversations_user_updated", table_name="conversations")
    op.drop_index("idx_conversations_user_mode", table_name="conversations")
    op.drop_table("conversations")
