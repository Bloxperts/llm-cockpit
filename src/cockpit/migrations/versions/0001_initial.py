"""initial schema: users, login_audit, model_tags, settings, model_config, model_perf

Revision ID: 0001
Revises:
Create Date: 2026-04-28

The six tables in scope for Sprint 2 / UC-08 Slice A. ADR-004 (role ladder)
governs `users.role`; ADR-005 governs `model_config` and `model_perf`.

`conversations`, `messages`, `metrics_snapshot`, and `admin_audit` are
deliberately omitted from this revision — they land with their owning UCs.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String, nullable=False, unique=True),
        sa.Column("pw_hash", sa.Text, nullable=False),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("must_change_password", sa.Integer, nullable=False, server_default="0"),
        sa.Column("password_changed_at", sa.DateTime, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("last_login_at", sa.DateTime, nullable=True),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
        sa.CheckConstraint("role IN ('chat', 'code', 'admin')", name="ck_users_role"),
    )

    op.create_table(
        "login_audit",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "ts",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("username", sa.String, nullable=True),
        sa.Column("success", sa.Integer, nullable=False),
        sa.Column("source_ip", sa.String, nullable=True),
        sa.Column("action", sa.String, nullable=False, server_default="login"),
    )

    op.create_table(
        "model_tags",
        sa.Column("model", sa.String, primary_key=True),
        sa.Column("tag", sa.String, nullable=False),
        sa.Column("source", sa.String, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint("tag IN ('chat', 'code', 'both')", name="ck_model_tags_tag"),
        sa.CheckConstraint(
            "source IN ('heuristic', 'admin')", name="ck_model_tags_source"
        ),
    )

    op.create_table(
        "settings",
        sa.Column("key", sa.String, primary_key=True),
        sa.Column("value", sa.Text, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )

    op.create_table(
        "model_config",
        sa.Column("model", sa.String, primary_key=True),
        sa.Column("placement", sa.String, nullable=False, server_default="on_demand"),
        sa.Column("keep_alive_seconds", sa.Integer, nullable=True),
        sa.Column("num_ctx_default", sa.Integer, nullable=True),
        sa.Column("single_flight", sa.Integer, nullable=False, server_default="0"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint(
            "placement IN ('on_demand', 'gpu0', 'gpu1', 'gpu2', 'gpu3', "
            "'multi_gpu', 'available')",
            name="ck_model_config_placement",
        ),
    )

    op.create_table(
        "model_perf",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("model", sa.String, nullable=False),
        sa.Column(
            "measured_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("cold_load_seconds", sa.Float, nullable=True),
        sa.Column("first_token_ms", sa.Float, nullable=True),
        sa.Column("throughput_tps", sa.Float, nullable=True),
        sa.Column("max_ctx_observed", sa.Integer, nullable=True),
        sa.Column("gpu_layout_json", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_index(
        "idx_model_perf_model_ts", "model_perf", ["model", "measured_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_model_perf_model_ts", table_name="model_perf")
    op.drop_table("model_perf")
    op.drop_table("model_config")
    op.drop_table("settings")
    op.drop_table("model_tags")
    op.drop_table("login_audit")
    op.drop_table("users")
