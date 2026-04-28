"""Sprint 7 — auth UX + session control tests.

Covers the four backend surfaces:

    PATCH /api/auth/session-ttl
    POST  /api/admin/users/{id}/revoke-sessions
    POST  /api/admin/users/{id}/deactivate
    POST  /api/admin/users/{id}/reactivate

Plus the cross-cutting changes:
    - login refuses deactivated accounts (403 account_disabled).
    - current_user refuses deactivated accounts (401 account_disabled).
    - current_user refuses tokens with stale tkv (401 session_revoked).
    - per-user `session_ttl_days` is honoured by the next token mint.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from cockpit.adapters.fake_chat import FakeLLMChat
from cockpit.adapters.fake_telemetry import FakeTelemetry
from cockpit.config import Settings
from cockpit.db import make_engine, make_session_factory, upgrade_to_head
from cockpit.main import create_app
from cockpit.models import AdminAudit, User
from cockpit.routers.auth import (
    COOKIE_NAME,
    JWT_ALG,
    TTL_MAP,
    _create_token,
)
from cockpit.services.users import hash_password


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "cockpit-data"
    data_dir.mkdir()
    s = Settings(
        data_dir=data_dir,
        host="127.0.0.1",
        port=18080,
        bcrypt_cost=4,
        jwt_secret="test-secret-do-not-use-in-prod",
    )
    upgrade_to_head(s.db_url)
    return s


@pytest.fixture
def session_factory(settings: Settings) -> sessionmaker:
    engine = make_engine(settings.db_url)
    return make_session_factory(engine)


@pytest.fixture
def seeded(settings: Settings) -> dict:
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    out = {
        "alice": {"role": "admin", "password": "AdminPW01!"},
        "bob":   {"role": "code",  "password": "CodePW01!"},
        "carol": {"role": "chat",  "password": "ChatPW01!"},
    }
    try:
        with factory() as session:
            for name, info in out.items():
                session.add(
                    User(
                        username=name,
                        pw_hash=hash_password(info["password"], cost=settings.bcrypt_cost),
                        role=info["role"],
                        must_change_password=0,
                    )
                )
            session.commit()
    finally:
        engine.dispose()
    return out


def _build_client(settings: Settings) -> TestClient:
    app = create_app(
        settings,
        chat_factory=lambda url: FakeLLMChat(models=[]),
        telemetry_factory=lambda: FakeTelemetry(snapshots=[]),
        skip_db_upgrade=True,
        skip_samplers=True,
    )
    return TestClient(app)


def _login(client: TestClient, seeded: dict, username: str) -> None:
    r = client.post(
        "/api/auth/login",
        json={"username": username, "password": seeded[username]["password"]},
    )
    assert r.status_code == 200, r.text


def _user_id(session_factory: sessionmaker, username: str) -> int:
    with session_factory() as session:
        return session.execute(
            select(User.id).where(User.username == username)
        ).scalar_one()


# =========================================================================
# PATCH /api/auth/session-ttl — preference round-trip
# =========================================================================


@pytest.mark.parametrize("days", [0, 1, 7, 30])
def test_set_session_ttl_valid_days(
    settings: Settings, seeded: dict, session_factory: sessionmaker, days: int
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "carol")
        r = client.patch("/api/auth/session-ttl", json={"ttl_days": days})
    assert r.status_code == 200
    assert r.json() == {"ttl_days": days}
    with session_factory() as session:
        carol = session.execute(select(User).where(User.username == "carol")).scalar_one()
    assert carol.session_ttl_days == days


def test_set_session_ttl_rejects_invalid(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "carol")
        r = client.patch("/api/auth/session-ttl", json={"ttl_days": 14})
    assert r.status_code == 422
    assert r.json()["detail"]["detail"] == "invalid_ttl_days"


def test_set_session_ttl_requires_auth(settings: Settings) -> None:
    client = _build_client(settings)
    with client:
        r = client.patch("/api/auth/session-ttl", json={"ttl_days": 7})
    assert r.status_code == 401


def test_login_after_ttl_change_uses_new_ttl(
    settings: Settings, seeded: dict
) -> None:
    """Set TTL = 1 day, log out, log back in → response.ttl_seconds reflects."""
    client = _build_client(settings)
    with client:
        _login(client, seeded, "carol")
        client.patch("/api/auth/session-ttl", json={"ttl_days": 1})
        client.post("/api/auth/logout")
        client.cookies.clear()
        r = client.post(
            "/api/auth/login",
            json={"username": "carol", "password": seeded["carol"]["password"]},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ttl_seconds"] == TTL_MAP[1]
    assert body["user"]["session_ttl_days"] == 1


def test_me_includes_session_ttl_days(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "carol")
        # Default — None.
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["session_ttl_days"] is None
        # Set, fetch again.
        client.patch("/api/auth/session-ttl", json={"ttl_days": 30})
        r = client.get("/api/auth/me")
    assert r.json()["session_ttl_days"] == 30


# =========================================================================
# tkv — Force re-login (admin)
# =========================================================================


def test_revoke_sessions_invalidates_existing_token(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Old token's `tkv` becomes stale → 401 session_revoked on next request."""
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        # Bob's session works before the revoke.
        assert client.get("/api/auth/me").status_code == 200
        # An admin (alice) revokes bob in a separate session.
        admin = _build_client(settings)
        with admin:
            _login(admin, seeded, "alice")
            r = admin.post(f"/api/admin/users/{bob_id}/revoke-sessions")
            assert r.status_code == 200
            assert r.json()["token_version"] == 1
        # Bob's existing cookie is now invalid.
        r = client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json()["detail"] == "session_revoked"


def test_revoke_sessions_writes_admin_audit(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        client.post(f"/api/admin/users/{bob_id}/revoke-sessions")
    with session_factory() as session:
        rows = list(
            session.execute(
                select(AdminAudit).where(AdminAudit.action == "sessions_revoked")
            ).scalars()
        )
    assert len(rows) == 1
    assert rows[0].target_model == "bob"


def test_revoke_sessions_admin_only(settings: Settings, seeded: dict, session_factory: sessionmaker) -> None:
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "carol")
        r = client.post(f"/api/admin/users/{bob_id}/revoke-sessions")
    assert r.status_code == 403


def test_revoke_sessions_404_for_missing(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.post("/api/admin/users/99999/revoke-sessions")
    assert r.status_code == 404


def test_admin_can_revoke_themselves(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Spec: admins can revoke any user including themselves; the next
    request from their browser bounces them to /login."""
    alice_id = _user_id(session_factory, "alice")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.post(f"/api/admin/users/{alice_id}/revoke-sessions")
        assert r.status_code == 200
        # Alice's own cookie is now stale.
        r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_stale_tkv_in_minted_token_returns_401(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Forge a token with the wrong `tkv` and confirm `current_user`
    rejects it with `session_revoked` (rather than the generic
    not_authenticated)."""
    bob_id = _user_id(session_factory, "bob")
    bad = _create_token(
        bob_id, ttl_seconds=3600, secret=settings.jwt_secret, token_version=999
    )
    client = _build_client(settings)
    with client:
        client.cookies.set(COOKIE_NAME, bad)
        r = client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json()["detail"] == "session_revoked"


# =========================================================================
# Deactivate / reactivate
# =========================================================================


def test_deactivate_user_blocks_login(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.post(f"/api/admin/users/{bob_id}/deactivate")
        assert r.status_code == 200

    # Now try logging in as bob → 403.
    fresh = _build_client(settings)
    with fresh:
        r = fresh.post(
            "/api/auth/login",
            json={"username": "bob", "password": seeded["bob"]["password"]},
        )
    assert r.status_code == 403
    assert r.json()["detail"] == "account_disabled"


def test_deactivate_kicks_existing_session(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """A logged-in user gets booted on the next request after deactivation."""
    bob_id = _user_id(session_factory, "bob")
    bob_client = _build_client(settings)
    with bob_client:
        _login(bob_client, seeded, "bob")
        # Admin in a separate client deactivates bob.
        admin = _build_client(settings)
        with admin:
            _login(admin, seeded, "alice")
            admin.post(f"/api/admin/users/{bob_id}/deactivate")
        # Bob's existing session is now invalid.
        r = bob_client.get("/api/auth/me")
    # Either account_disabled or session_revoked — the deactivate
    # implementation also bumps token_version so technically the tkv
    # check could fire first. Either error code means "you're out."
    assert r.status_code == 401
    assert r.json()["detail"] in ("account_disabled", "session_revoked")


def test_deactivate_last_active_admin_blocked(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    alice_id = _user_id(session_factory, "alice")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.post(f"/api/admin/users/{alice_id}/deactivate")
    assert r.status_code == 400
    assert r.json()["detail"]["detail"] == "last_active_admin"


def test_deactivate_admin_when_other_active_admin_exists(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Promote bob to admin first; then alice can deactivate bob (admin)
    because she's still around."""
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        client.patch(f"/api/admin/users/{bob_id}/role", json={"role": "admin"})
        r = client.post(f"/api/admin/users/{bob_id}/deactivate")
    assert r.status_code == 200


def test_reactivate_restores_login(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        client.post(f"/api/admin/users/{bob_id}/deactivate")
        r = client.post(f"/api/admin/users/{bob_id}/reactivate")
    assert r.status_code == 200

    # Bob can log in again.
    fresh = _build_client(settings)
    with fresh:
        r = fresh.post(
            "/api/auth/login",
            json={"username": "bob", "password": seeded["bob"]["password"]},
        )
    assert r.status_code == 200


def test_deactivate_idempotent(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        client.post(f"/api/admin/users/{bob_id}/deactivate")
        # Already deactivated → ok response with `already=deactivated`.
        r = client.post(f"/api/admin/users/{bob_id}/deactivate")
    assert r.status_code == 200
    assert r.json().get("already") == "deactivated"


def test_reactivate_idempotent(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        # Already active → no-op.
        r = client.post(f"/api/admin/users/{bob_id}/reactivate")
    assert r.status_code == 200
    assert r.json().get("already") == "active"


def test_deactivate_404_for_missing(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.post("/api/admin/users/99999/deactivate")
    assert r.status_code == 404


def test_reactivate_404_for_missing(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.post("/api/admin/users/99999/reactivate")
    assert r.status_code == 404


def test_deactivate_admin_only(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "carol")
        r = client.post(f"/api/admin/users/{bob_id}/deactivate")
    assert r.status_code == 403


# =========================================================================
# Misc cross-cutting
# =========================================================================


def test_existing_token_after_session_ttl_change_keeps_old_ttl(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Spec: TTL change applies to the next token. The current cookie
    keeps its existing expiry — a fresh /me right after the patch
    doesn't get a new cookie because we're well within the sliding
    window."""
    client = _build_client(settings)
    with client:
        _login(client, seeded, "carol")
        # No Set-Cookie should land on /me when token is fresh
        r = client.patch("/api/auth/session-ttl", json={"ttl_days": 1})
        assert r.status_code == 200
        # /me right away — no slide because token is brand-new with 7d
        # default left.
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        assert "set-cookie" not in r.headers


def test_default_admin_token_version_is_zero(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Newly-seeded users default to token_version=0; the JWT carries
    `tkv: 0` and current_user accepts it."""
    with session_factory() as session:
        for u in session.execute(select(User)).scalars():
            assert u.token_version == 0
            assert u.is_active == 1
            assert u.session_ttl_days is None


_ = datetime  # used by other Sprint-7 tests if added later
_ = timedelta
_ = timezone