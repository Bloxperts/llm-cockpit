"""auth UX: token_version + session_ttl_days + is_active on users

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-28

Sprint 7 — adds three columns to `users` for the auth-UX surface:

- `token_version` (INT, NOT NULL, default 0) — incremented to invalidate
  every JWT issued for the user before the bump. Embedded in the JWT as
  the `tkv` claim and re-checked in `current_user`. Powers the "Force
  re-login" admin action and the deactivation auto-revoke.
- `session_ttl_days` (INT, NULLABLE) — the user's preferred JWT lifetime
  in days (1, 7, 30, or 0 = unlimited). NULL means "use the system
  default of 7 days" — the column is nullable so existing users on
  develop continue to behave as before until they pick a value.
- `is_active` (INT, NOT NULL, default 1) — soft-deactivation flag,
  distinct from `deleted_at` (which is a permanent removal). A
  deactivated account can be reactivated by an admin; a deleted one
  cannot. Login + `current_user` both reject deactivated accounts.

All three are nullable-safe / default-safe for the in-place ALTER on
SQLite, so no batch_alter_table dance is required.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "session_ttl_days",
            sa.Integer,
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "is_active",
            sa.Integer,
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    # SQLite < 3.35 needs batch mode for DROP COLUMN; alembic env.py
    # already enables `render_as_batch=True` so this is fine on the
    # development machine. Production never downgrades.
    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_active")
        batch.drop_column("session_ttl_days")
        batch.drop_column("token_version")
