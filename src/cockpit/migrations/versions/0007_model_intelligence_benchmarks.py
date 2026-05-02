"""model intelligence benchmark profiles

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision: str | None = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_perf") as batch:
        batch.add_column(sa.Column("warm_load_seconds", sa.Float(), nullable=True))
        batch.add_column(sa.Column("benchmark_profile", sa.String(), nullable=True))

    op.execute(
        "UPDATE model_perf SET benchmark_profile = COALESCE(placement_tested, 'on_demand') "
        "WHERE benchmark_profile IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("model_perf") as batch:
        batch.drop_column("benchmark_profile")
        batch.drop_column("warm_load_seconds")
