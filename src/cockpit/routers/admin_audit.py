"""Unified admin audit log (UC-10).

Merges `login_audit` and `admin_audit` into a single time-sorted feed
with optional filters by `action` and `username`. Two endpoints:

    GET /api/admin/audit               JSON + pagination
    GET /api/admin/audit/export        text/csv attachment, all rows

SQLite handles UNION-with-ORDER-BY-LIMIT poorly across heterogeneous
schemas, so we run two separate queries, merge in Python, sort by `ts`,
and slice for pagination. v0.1's tables are tiny — even a heavily-used
cockpit accumulates a few hundred rows a day. Revisit if either table
grows past ~100k rows.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from cockpit.deps import get_session
from cockpit.models import AdminAudit, LoginAudit, User
from cockpit.routers.auth import require_role
from cockpit.schemas import AuditEntry, AuditResponse

router = APIRouter()


# --- Helpers --------------------------------------------------------------


def _username_for(actor_id: int | None, by_id: dict[int, str]) -> str | None:
    if actor_id is None:
        return None
    return by_id.get(actor_id)


def _parse_details(blob: str | None) -> dict[str, Any] | None:
    if not blob:
        return None
    try:
        return json.loads(blob)
    except (TypeError, ValueError):
        return None


def _collect_entries(
    db: Session,
    *,
    action_filter: str | None,
    username_filter: str | None,
) -> list[AuditEntry]:
    """Run the two queries, merge, sort ts-desc."""
    # Resolve actor_id -> username up front for admin_audit rows.
    by_id: dict[int, str] = {
        u.id: u.username
        for u in db.execute(select(User.id, User.username)).all()
    }

    out: list[AuditEntry] = []

    # admin_audit rows --------------------------------------------------
    admin_q = select(AdminAudit)
    if action_filter:
        admin_q = admin_q.where(AdminAudit.action == action_filter)
    for row in db.execute(admin_q).scalars():
        actor = _username_for(row.actor_id, by_id)
        if username_filter and (actor != username_filter):
            continue
        out.append(
            AuditEntry(
                source="admin",
                ts=row.ts,
                actor=actor,
                action=row.action,
                target=row.target_model,
                details=_parse_details(row.details_json),
                source_ip=row.source_ip,
            )
        )

    # login_audit rows --------------------------------------------------
    login_q = select(LoginAudit)
    if action_filter:
        login_q = login_q.where(LoginAudit.action == action_filter)
    if username_filter:
        login_q = login_q.where(LoginAudit.username == username_filter)
    for row in db.execute(login_q).scalars():
        out.append(
            AuditEntry(
                source="login",
                ts=row.ts,
                actor=row.username,
                action=row.action,
                target=row.username,
                details={"success": bool(row.success)},
                source_ip=row.source_ip,
            )
        )

    out.sort(key=lambda e: e.ts, reverse=True)
    return out


# --- Endpoints ------------------------------------------------------------


@router.get(
    "",
    response_model=AuditResponse,
    summary="Unified login + admin audit feed (admin).",
)
def get_audit(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    action: str | None = None,
    username: str | None = None,
    actor: User = Depends(require_role("admin")),
    db: Session = Depends(get_session),
) -> AuditResponse:
    entries = _collect_entries(db, action_filter=action, username_filter=username)
    total = len(entries)
    start = (page - 1) * per_page
    end = start + per_page
    return AuditResponse(
        entries=entries[start:end],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get(
    "/export",
    summary="Audit log CSV export (admin).",
)
def export_audit(
    action: str | None = None,
    username: str | None = None,
    actor: User = Depends(require_role("admin")),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    entries = _collect_entries(db, action_filter=action, username_filter=username)

    def gen() -> Any:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["ts", "source", "actor", "action", "target", "source_ip", "details"]
        )
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        for e in entries:
            writer.writerow(
                [
                    e.ts.isoformat() if isinstance(e.ts, datetime) else str(e.ts),
                    e.source,
                    e.actor or "",
                    e.action,
                    e.target or "",
                    e.source_ip or "",
                    json.dumps(e.details) if e.details is not None else "",
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit.csv"},
    )
