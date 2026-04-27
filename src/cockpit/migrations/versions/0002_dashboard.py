"""UC-02 dashboard tables: metrics_snapshot + admin_audit

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-28

Adds the two tables UC-02 (Sprint 3) writes:

- `metrics_snapshot` — one row per GPU per 5 s sample (GpuSampler in
  services/metrics.py). Indexes on `ts` and `(gpu_index, ts)` for the
  Sprint 5 dashboard-history queries (UC-03).
- `admin_audit` — one row per state-changing admin action (place /
  pull / delete / settings_patch / perf_test). Indexes on `ts` and
  `action` for the audit-log filter view (UC-10).

These tables are append-only; no foreign keys to keep the schema simple
(actor_id can become orphan if a user is hard-deleted, which is fine for
audit purposes — the username lives in the JSON details).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metrics_snapshot",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "ts",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("gpu_index", sa.Integer, nullable=False),
        sa.Column("vram_used_mb", sa.Integer, nullable=False),
        sa.Column("vram_total_mb", sa.Integer, nullable=False),
        sa.Column("temp_c", sa.Float, nullable=True),
        sa.Column("power_w", sa.Float, nullable=True),
    )
    op.create_index("idx_metrics_snapshot_ts", "metrics_snapshot", ["ts"])
    op.create_index(
        "idx_metrics_snapshot_gpu_ts", "metrics_snapshot", ["gpu_index", "ts"]
    )

    op.create_table(
        "admin_audit",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "ts",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("actor_id", sa.Integer, nullable=True),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("target_model", sa.String, nullable=True),
        sa.Column("details_json", sa.Text, nullable=True),
        sa.Column("source_ip", sa.String, nullable=True),
    )
    op.create_index("idx_admin_audit_ts", "admin_audit", ["ts"])
    op.create_index("idx_admin_audit_action", "admin_audit", ["action"])


def downgrade() -> None:
    op.drop_index("idx_admin_audit_action", table_name="admin_audit")
    op.drop_index("idx_admin_audit_ts", table_name="admin_audit")
    op.drop_table("admin_audit")
    op.drop_index("idx_metrics_snapshot_gpu_ts", table_name="metrics_snapshot")
    op.drop_index("idx_metrics_snapshot_ts", table_name="metrics_snapshot")
    op.drop_table("metrics_snapshot")
