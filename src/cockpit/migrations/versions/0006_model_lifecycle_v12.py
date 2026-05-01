"""model lifecycle v1.2 metadata and dynamic placement

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision: str | None = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_config") as batch:
        batch.drop_constraint("ck_model_config_placement", type_="check")
        batch.add_column(
            sa.Column(
                "keep_alive_mode",
                sa.String(),
                nullable=False,
                server_default="default",
            )
        )
        batch.create_check_constraint(
            "ck_model_config_keep_alive_mode",
            "keep_alive_mode IN ('default', 'finite', 'permanent', 'unload')",
        )
    op.execute(
        "UPDATE model_config SET keep_alive_mode = CASE "
        "WHEN placement IN ('on_demand', 'available') THEN 'unload' "
        "WHEN keep_alive_seconds IS NOT NULL THEN 'finite' "
        "ELSE 'default' END"
    )

    op.create_table(
        "model_metadata",
        sa.Column("model", sa.String(), primary_key=True),
        sa.Column("parameter_size", sa.String(), nullable=True),
        sa.Column("quantization_level", sa.String(), nullable=True),
        sa.Column("architecture_context_length", sa.Integer(), nullable=True),
        sa.Column("capabilities_json", sa.Text(), nullable=True),
        sa.Column("release_date", sa.DateTime(), nullable=True),
        sa.Column("release_date_source", sa.String(), nullable=True),
        sa.Column("registry_updated_at", sa.DateTime(), nullable=True),
        sa.Column("local_modified_at", sa.DateTime(), nullable=True),
        sa.Column(
            "metadata_refreshed_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )

    with op.batch_alter_table("model_perf") as batch:
        batch.add_column(sa.Column("placement_tested", sa.String(), nullable=True))
        batch.add_column(sa.Column("gpu_count_at_test", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("num_ctx_used", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("keep_alive_used", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("model_perf") as batch:
        batch.drop_column("keep_alive_used")
        batch.drop_column("num_ctx_used")
        batch.drop_column("gpu_count_at_test")
        batch.drop_column("placement_tested")

    op.drop_table("model_metadata")

    with op.batch_alter_table("model_config") as batch:
        batch.drop_constraint("ck_model_config_keep_alive_mode", type_="check")
        batch.drop_column("keep_alive_mode")
        batch.create_check_constraint(
            "ck_model_config_placement",
            "placement IN ('on_demand', 'gpu0', 'gpu1', 'gpu2', 'gpu3', 'multi_gpu', 'available')",
        )
