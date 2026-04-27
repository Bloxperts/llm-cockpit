"""User services: bcrypt hashing + admin seed.

Slice A scope: just enough to seed the bootstrap admin per ADR-003 §3.
The full user CRUD lands with UC-06.
"""

from __future__ import annotations

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
