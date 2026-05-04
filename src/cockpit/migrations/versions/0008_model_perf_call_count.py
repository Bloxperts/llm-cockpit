"""model perf call count

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision: str | None = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_perf") as batch:
        batch.add_column(
            sa.Column("call_count", sa.Integer(), nullable=False, server_default="0")
        )

    # Backfill old perf rows so the model-card KPI reflects historical harness
    # traffic too. A successful profile normally performs unload, cold load,
    # warm load, three throughput runs, one context probe, and a placement
    # restore around the run; failed rows get the minimum one attempted call.
    op.execute(
        """
        UPDATE model_perf
        SET call_count = CASE
            WHEN notes IS NULL THEN 8
            ELSE 1
        END
        WHERE call_count = 0
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("model_perf") as batch:
        batch.drop_column("call_count")
