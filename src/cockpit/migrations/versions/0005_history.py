"""dashboard history: metrics_snapshot_minute + metrics_snapshot_hour + messages.ts index

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-28

Sprint 8 (UC-03) — adds two down-sampled aggregation tables fed by the
`MinuteAggregator` and `HourAggregator` background tasks, plus a
standalone index on `messages.ts` for the call-rate / latency / tokens
history queries.

Tables
------
- `metrics_snapshot_minute(bucket_ts, gpu_index, vram_used_mb_avg,
  temp_c_avg, temp_c_max, power_w_avg, sample_count)` — one row per
  (closed minute, GPU). Holds the last 30 d. Drives the 24 h chart range.
- `metrics_snapshot_hour(...)` — same shape, one row per (closed hour,
  GPU). Drives the 7 d chart range. Effectively unbounded retention but
  in practice tiny (24 × 7 × `n_gpu` ≈ a few hundred rows for a 7 d
  window — we don't prune for the moment).

Both tables carry a UNIQUE(bucket_ts, gpu_index) constraint so the
aggregators can use `INSERT OR IGNORE` for idempotent re-runs (a
restarted process re-aggregates the last completed bucket without
producing duplicates).

Indexes on the new tables: `(gpu_index, bucket_ts)` for the per-GPU
range scans the history endpoint runs.

Existing indexes (kept):
- `idx_metrics_snapshot_ts` and `idx_metrics_snapshot_gpu_ts` already
  cover the raw-table reads the MinuteAggregator does.
- `messages` has `idx_messages_conversation_ts (conversation_id, ts)`,
  but no standalone `ts` index. Per SQLite's leftmost-prefix rule the
  composite cannot serve a `ts`-only `WHERE` clause, so the call-rate
  query would fall back to a full scan. This migration adds
  `idx_messages_ts` to fix that.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision: str | None = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metrics_snapshot_minute",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("bucket_ts", sa.DateTime, nullable=False),
        sa.Column("gpu_index", sa.Integer, nullable=False),
        sa.Column("vram_used_mb_avg", sa.Float, nullable=False),
        sa.Column("temp_c_avg", sa.Float, nullable=True),
        sa.Column("temp_c_max", sa.Float, nullable=True),
        sa.Column("power_w_avg", sa.Float, nullable=True),
        sa.Column("sample_count", sa.Integer, nullable=False),
        sa.UniqueConstraint("bucket_ts", "gpu_index", name="uq_msm_bucket_gpu"),
    )
    op.create_index(
        "idx_msm_gpu_ts",
        "metrics_snapshot_minute",
        ["gpu_index", "bucket_ts"],
    )

    op.create_table(
        "metrics_snapshot_hour",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("bucket_ts", sa.DateTime, nullable=False),
        sa.Column("gpu_index", sa.Integer, nullable=False),
        sa.Column("vram_used_mb_avg", sa.Float, nullable=False),
        sa.Column("temp_c_avg", sa.Float, nullable=True),
        sa.Column("temp_c_max", sa.Float, nullable=True),
        sa.Column("power_w_avg", sa.Float, nullable=True),
        sa.Column("sample_count", sa.Integer, nullable=False),
        sa.UniqueConstraint("bucket_ts", "gpu_index", name="uq_msh_bucket_gpu"),
    )
    op.create_index(
        "idx_msh_gpu_ts",
        "metrics_snapshot_hour",
        ["gpu_index", "bucket_ts"],
    )

    op.create_index("idx_messages_ts", "messages", ["ts"])


def downgrade() -> None:
    op.drop_index("idx_messages_ts", table_name="messages")
    op.drop_index("idx_msh_gpu_ts", table_name="metrics_snapshot_hour")
    op.drop_table("metrics_snapshot_hour")
    op.drop_index("idx_msm_gpu_ts", table_name="metrics_snapshot_minute")
    op.drop_table("metrics_snapshot_minute")
