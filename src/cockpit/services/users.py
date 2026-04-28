"""User services: bcrypt hashing + admin seed + lookup helpers.

Originally Slice A — seed only. UC-01 added `get_user_by_id`,
`get_user_by_username`, and `update_last_login`. UC-09 added
`update_password`. UC-06 (Sprint 6) adds:

- `get_token_totals(session, user_id) -> (in, out)` for the user-list view.
- `get_token_totals_bulk(session) -> dict[user_id, (in, out)]` to avoid
  N+1 queries when rendering the full user list.
- `count_active_admins(session)` for the last-admin-demotion guard.
- `create_managed_user(session, ...)` — the admin-creates-user path with
  must_change_password=True per UC-09.
- `change_role(session, user, role)` and `soft_delete(session, user)`.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import bcrypt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cockpit.models import Conversation, Message, User

USERNAME_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{1,30}$")
VALID_ROLES = ("chat", "code", "admin")

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "ollama"


def hash_password(plaintext: str, *, cost: int = 12) -> str:
    """bcrypt-hash a password. Returns the encoded hash as a UTF-8 string."""
    salt = bcrypt.gensalt(rounds=cost)
    return bcrypt.hashpw(plaintext.encode("utf-8"), salt).decode("utf-8")


def verify_password(plaintext: str, pw_hash: str) -> bool:
    return bcrypt.checkpw(plaintext.encode("utf-8"), pw_hash.encode("utf-8"))


def admin_exists(session: Session) -> bool:
    return session.query(User).filter_by(username=DEFAULT_ADMIN_USERNAME).first() is not None


def seed_admin(
    session: Session,
    *,
    password: str = DEFAULT_ADMIN_PASSWORD,
    bcrypt_cost: int = 12,
) -> User:
    """Insert the bootstrap admin if it doesn't exist; otherwise return the
    existing row untouched. Idempotent — UC-08 AC-4 requires re-running `init`
    not to overwrite the admin's password.
    """
    existing = session.query(User).filter_by(username=DEFAULT_ADMIN_USERNAME).first()
    if existing is not None:
        return existing

    user = User(
        username=DEFAULT_ADMIN_USERNAME,
        pw_hash=hash_password(password, cost=bcrypt_cost),
        role="admin",
        must_change_password=1,
    )
    session.add(user)
    session.flush()
    return user


def get_user_by_id(session: Session, user_id: int) -> User | None:
    return session.query(User).filter_by(id=user_id).first()


def get_user_by_username(session: Session, username: str) -> User | None:
    return session.query(User).filter_by(username=username).first()


def update_last_login(session: Session, user: User) -> None:
    user.last_login_at = datetime.now(timezone.utc)
    session.flush()


def update_password(
    session: Session,
    user: User,
    new_password: str,
    *,
    bcrypt_cost: int = 12,
) -> None:
    """UC-09 helper: hash, store, clear `must_change_password`, stamp
    `password_changed_at`. Doesn't write `login_audit` — the router does that
    so the source IP and action discriminator are bound to the request.
    """
    user.pw_hash = hash_password(new_password, cost=bcrypt_cost)
    user.must_change_password = 0
    user.password_changed_at = datetime.now(timezone.utc)
    session.flush()


# --- UC-06 admin user management -----------------------------------------


def is_valid_username(username: str) -> bool:
    return bool(USERNAME_PATTERN.match(username))


def is_valid_role(role: str) -> bool:
    return role in VALID_ROLES


def count_active_admins(session: Session) -> int:
    """Count not-soft-deleted users with role='admin'. Used by the
    last-admin-demotion / cannot-delete-self guard.
    """
    return (
        session.execute(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.deleted_at.is_(None))
        ).scalar_one()
    )


def create_managed_user(
    session: Session,
    *,
    username: str,
    password: str,
    role: str = "chat",
    bcrypt_cost: int = 12,
) -> User:
    """Admin-created accounts land with `must_change_password=1` per
    UC-09 + ADR-003 §3. Caller is responsible for: validating inputs,
    checking uniqueness, writing the audit row, and committing the
    transaction.
    """
    user = User(
        username=username,
        pw_hash=hash_password(password, cost=bcrypt_cost),
        role=role,
        must_change_password=1,
    )
    session.add(user)
    session.flush()
    return user


def change_role(session: Session, user: User, role: str) -> None:
    user.role = role
    session.flush()


def soft_delete(session: Session, user: User) -> None:
    user.deleted_at = datetime.now(timezone.utc)
    session.flush()


def reset_password_admin(
    session: Session,
    user: User,
    new_password: str,
    *,
    bcrypt_cost: int = 12,
) -> None:
    """Admin password reset: hash + flip `must_change_password=1` so the
    user is forced through the UC-09 change flow on next login. Distinct
    from `update_password` (which is the user-driven self-change path).
    """
    user.pw_hash = hash_password(new_password, cost=bcrypt_cost)
    user.must_change_password = 1
    user.password_changed_at = None
    session.flush()


def get_token_totals(session: Session, user_id: int) -> tuple[int, int]:
    """Sum `usage_in` + `usage_out` over assistant messages on the user's
    conversations. Returns (0, 0) if the user has no conversations / no
    completed assistant turns.

    UC-06 admin user table renders these per-user. Used as a one-off
    fetch in tests; the production list endpoint uses
    `get_token_totals_bulk` instead.
    """
    row = session.execute(
        select(
            func.coalesce(func.sum(Message.usage_in), 0),
            func.coalesce(func.sum(Message.usage_out), 0),
        )
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.user_id == user_id, Message.role == "assistant")
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


def get_token_totals_bulk(session: Session) -> dict[int, tuple[int, int]]:
    """One query, GROUP BY conversation.user_id. Avoids N+1 when rendering
    the full user list. Returns a dict; users with no rows are absent
    (caller defaults to (0, 0)).
    """
    rows = session.execute(
        select(
            Conversation.user_id,
            func.coalesce(func.sum(Message.usage_in), 0),
            func.coalesce(func.sum(Message.usage_out), 0),
        )
        .select_from(Conversation)
        .join(Message, Message.conversation_id == Conversation.id)
        .where(Message.role == "assistant")
        .group_by(Conversation.user_id)
    ).all()
    return {int(r[0]): (int(r[1] or 0), int(r[2] or 0)) for r in rows}
