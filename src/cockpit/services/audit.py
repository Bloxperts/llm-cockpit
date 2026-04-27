"""Admin-audit helpers.

Per UC-02 functional spec §Backend logic — every state-changing admin
action on the dashboard writes one row to `admin_audit`. Per DP-013, this
service is the only writer of that table.

The action vocabulary is open-ended (string column, no CHECK constraint
on purpose so future UCs can add new actions without a migration). v0.1
uses: `model_place`, `model_perf_test`, `model_pull`, `model_delete`,
`model_settings_patch`. UC-10 (Sprint 7) reads everything for the audit
log filter view.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from cockpit.models import AdminAudit


def write_admin_audit(
    session: Session,
    *,
    actor_id: int | None,
    action: str,
    target_model: str | None = None,
    details: dict[str, Any] | None = None,
    source_ip: str | None = None,
) -> AdminAudit:
    """Insert one `admin_audit` row. Caller is responsible for committing
    the surrounding transaction.
    """
    row = AdminAudit(
        actor_id=actor_id,
        action=action,
        target_model=target_model,
        details_json=json.dumps(details, default=str) if details is not None else None,
        source_ip=source_ip,
    )
    session.add(row)
    session.flush()
    return row
