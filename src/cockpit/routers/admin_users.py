"""Admin user-management router (UC-06).

All endpoints are gated by `require_role_settled("admin")` (ADR-004 §4).
Per DP-013, only this router writes to `users` (besides the auth router's
self-service password change and the bootstrap seed). Every state-changing
operation writes one row to `admin_audit` per DP-002.

Audit shape:
- AdminAudit.action: 'user_created' / 'role_changed' / 'password_reset_by_admin' / 'user_deleted'.
- AdminAudit.target_model: re-purposed to carry the target username (the
  AdminAudit table's `target_user_id` column from the spec data model
  isn't yet on this branch — UC-10's audit-log slice can add the column
  via migration if/when needed; details_json carries the int id today).
- AdminAudit.details_json: {"target_user_id": int, "username": str, ...}.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from cockpit.deps import get_session
from cockpit.models import User
from cockpit.routers.auth import require_role_settled
from cockpit.schemas import (
    CreateUserRequest,
    PatchRoleRequest,
    ResetPasswordRequest,
    UserSummary,
)
from cockpit.services.audit import write_admin_audit
from cockpit.services.users import (
    change_role as svc_change_role,
)
from cockpit.services.users import (
    count_active_admins,
    create_managed_user,
    get_token_totals_bulk,
    is_valid_role,
    is_valid_username,
    reset_password_admin,
)
from cockpit.services.users import (
    soft_delete as svc_soft_delete,
)

log = logging.getLogger(__name__)
router = APIRouter()


def _client_ip(request: Request) -> str | None:
    if request.client is not None:
        return request.client.host
    return None


def _serialize_user(
    u: User, totals: dict[int, tuple[int, int]] | None = None
) -> UserSummary:
    tin, tout = (totals or {}).get(u.id, (0, 0))
    return UserSummary(
        id=u.id,
        username=u.username,
        role=u.role,
        must_change_password=bool(u.must_change_password),
        created_at=u.created_at,
        last_login_at=u.last_login_at,
        deleted_at=u.deleted_at,
        tokens_in=tin,
        tokens_out=tout,
    )


@router.get("", response_model=list[UserSummary], summary="List users (admin).")
def list_users(
    request: Request,
    include_deleted: bool = False,
    q: str | None = None,
    actor: User = Depends(require_role_settled("admin")),
    db: Session = Depends(get_session),
) -> list[UserSummary]:
    """List users with lifetime token totals.

    Filters:
      include_deleted=true  → include soft-deleted rows (audit view).
      q=<prefix>            → username starts-with filter (case-sensitive,
                              matches the username regex's lowercase shape).
    """
    stmt = select(User).order_by(User.id)
    if not include_deleted:
        stmt = stmt.where(User.deleted_at.is_(None))
    if q:
        stmt = stmt.where(User.username.like(f"{q}%"))
    users = list(db.execute(stmt).scalars())
    totals = get_token_totals_bulk(db)
    return [_serialize_user(u, totals) for u in users]


@router.post(
    "",
    response_model=UserSummary,
    status_code=201,
    summary="Create a managed user (admin).",
)
def create_user(
    body: CreateUserRequest,
    request: Request,
    actor: User = Depends(require_role_settled("admin")),
    db: Session = Depends(get_session),
) -> UserSummary:
    if not is_valid_username(body.username):
        raise HTTPException(
            400,
            detail={
                "detail": "invalid_username",
                "hint": "lowercase, start with a letter, [a-z0-9._-]{1,30}",
            },
        )
    if not is_valid_role(body.role):
        raise HTTPException(400, detail={"detail": "invalid_role"})
    existing = db.execute(
        select(User).where(User.username == body.username)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, detail={"detail": "username_exists"})

    user = create_managed_user(
        db, username=body.username, password=body.password, role=body.role
    )
    write_admin_audit(
        db,
        actor_id=actor.id,
        action="user_created",
        target_model=body.username,
        details={"target_user_id": user.id, "username": body.username, "role": body.role},
        source_ip=_client_ip(request),
    )
    db.commit()
    return _serialize_user(user)


@router.patch(
    "/{user_id}/role",
    response_model=UserSummary,
    summary="Change a user's role (admin).",
)
def patch_role(
    user_id: int,
    body: PatchRoleRequest,
    request: Request,
    actor: User = Depends(require_role_settled("admin")),
    db: Session = Depends(get_session),
) -> UserSummary:
    if not is_valid_role(body.role):
        raise HTTPException(400, detail={"detail": "invalid_role"})

    target = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if target is None or target.deleted_at is not None:
        raise HTTPException(404, detail="user_not_found")

    old_role = target.role
    if old_role == body.role:
        # No-op; don't write an audit row for an idempotent call.
        return _serialize_user(target)

    # Last-admin demotion guard: a former admin going to chat / code
    # shouldn't drop the last-admin count to zero.
    if old_role == "admin" and body.role != "admin":
        if count_active_admins(db) <= 1:
            raise HTTPException(409, detail={"detail": "cannot_demote_last_admin"})

    svc_change_role(db, target, body.role)
    write_admin_audit(
        db,
        actor_id=actor.id,
        action="role_changed",
        target_model=target.username,
        details={
            "target_user_id": target.id,
            "username": target.username,
            "old_role": old_role,
            "new_role": body.role,
        },
        source_ip=_client_ip(request),
    )
    db.commit()
    return _serialize_user(target)


@router.post(
    "/{user_id}/reset-password",
    summary="Admin password reset (forces UC-09 change-on-next-login).",
)
def reset_password(
    user_id: int,
    body: ResetPasswordRequest,
    request: Request,
    actor: User = Depends(require_role_settled("admin")),
    db: Session = Depends(get_session),
) -> dict:
    if len(body.new_password) < 8:
        raise HTTPException(400, detail={"detail": "too_short", "min": 8})

    target = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if target is None or target.deleted_at is not None:
        raise HTTPException(404, detail="user_not_found")

    reset_password_admin(db, target, body.new_password)
    write_admin_audit(
        db,
        actor_id=actor.id,
        action="password_reset_by_admin",
        target_model=target.username,
        details={"target_user_id": target.id, "username": target.username},
        source_ip=_client_ip(request),
    )
    db.commit()
    return {"ok": True}


@router.delete(
    "/{user_id}",
    status_code=204,
    summary="Soft-delete a user (admin).",
)
def delete_user(
    user_id: int,
    request: Request,
    actor: User = Depends(require_role_settled("admin")),
    db: Session = Depends(get_session),
) -> None:
    if user_id == actor.id:
        raise HTTPException(409, detail={"detail": "cannot_self_delete"})

    target = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if target is None or target.deleted_at is not None:
        raise HTTPException(404, detail="user_not_found")

    if target.role == "admin" and count_active_admins(db) <= 1:
        raise HTTPException(409, detail={"detail": "cannot_delete_last_admin"})

    svc_soft_delete(db, target)
    write_admin_audit(
        db,
        actor_id=actor.id,
        action="user_deleted",
        target_model=target.username,
        details={"target_user_id": target.id, "username": target.username, "role": target.role},
        source_ip=_client_ip(request),
    )
    db.commit()
    return None
