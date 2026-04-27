"""User services: bcrypt hashing + admin seed + lookup helpers.

Originally Slice A — seed only. UC-01 adds `get_user_by_id`,
`get_user_by_username`, and `update_last_login`. UC-09 adds
`update_password` (also clears the must_change_password flag).
The full admin user CRUD lands with UC-06.
"""

from __future__ import annotations

from datetime import datetime, timezone

import bcrypt
from sqlalchemy.orm import Session

from cockpit.models import User

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
