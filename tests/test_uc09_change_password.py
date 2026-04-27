"""UC-09 — first-login forced password change tests.

Maps to docs/specs/test/UC-09-first-login-password-change.md (T-01..T-10).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cockpit.adapters.fake_chat import FakeLLMChat
from cockpit.config import Settings
from cockpit.db import make_engine, make_session_factory, upgrade_to_head
from cockpit.main import create_app
from cockpit.models import LoginAudit, User
from cockpit.routers.auth import COOKIE_NAME
from cockpit.services.users import hash_password


# --- Fixtures -------------------------------------------------------------


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
def fresh_admin(settings: Settings) -> dict:
    """Seed a user with must_change_password=1, like the bootstrap admin."""
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    pw = "ollama"  # the literal default
    try:
        with factory() as session:
            session.add(
                User(
                    username="admin",
                    pw_hash=hash_password(pw, cost=settings.bcrypt_cost),
                    role="admin",
                    must_change_password=1,
                )
            )
            session.commit()
    finally:
        engine.dispose()
    return {"username": "admin", "password": pw}


@pytest.fixture
def settled_user(settings: Settings) -> dict:
    """Seed a user with must_change_password=0 — no forced change."""
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    pw = "AlreadyOK1!"
    try:
        with factory() as session:
            session.add(
                User(
                    username="settled",
                    pw_hash=hash_password(pw, cost=settings.bcrypt_cost),
                    role="chat",
                    must_change_password=0,
                )
            )
            session.commit()
    finally:
        engine.dispose()
    return {"username": "settled", "password": pw}


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(
        settings,
        chat_factory=lambda url: FakeLLMChat(models=[]),
        skip_db_upgrade=True,
    )
    with TestClient(app) as tc:
        yield tc


def _audit_rows(settings: Settings) -> list[LoginAudit]:
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            return list(session.query(LoginAudit).order_by(LoginAudit.id).all())
    finally:
        engine.dispose()


def _login(client: TestClient, username: str, password: str):
    return client.post("/api/auth/login", json={"username": username, "password": password})


# --- T-01 — login succeeds, but next request is 409 -----------------------


def test_login_succeeds_then_subsequent_request_is_409(
    client: TestClient, fresh_admin: dict
) -> None:
    r = _login(client, fresh_admin["username"], fresh_admin["password"])
    assert r.status_code == 200
    assert r.json()["user"]["must_change_password"] is True

    # /me does NOT use current_user_must_be_settled (so the frontend can
    # discover the flag); but a *settled* route — like /logout — does.
    r_logout = client.post("/api/auth/logout")
    assert r_logout.status_code == 409
    body = r_logout.json()
    assert body == {"detail": "must_change_password"}
    assert r_logout.headers.get("www-authenticate") == "ChangePassword"


# --- /me is intentionally NOT settled-gated (UC-09 spec §dependency) -----


def test_me_is_not_settled_gated(client: TestClient, fresh_admin: dict) -> None:
    """Per UC-09 functional spec: /me is one of the two exceptions to the
    settled gate so the frontend can read the must_change_password flag.
    """
    _login(client, fresh_admin["username"], fresh_admin["password"])
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["must_change_password"] is True


# --- T-03 — cannot reuse the literal default password --------------------


def test_change_password_rejects_literal_ollama(
    client: TestClient, fresh_admin: dict
) -> None:
    _login(client, fresh_admin["username"], fresh_admin["password"])
    r = client.post(
        "/api/auth/change-password",
        json={"new_password": "ollama", "confirm_password": "ollama"},
    )
    assert r.status_code == 400
    assert r.json() == {"detail": "cannot_reuse_default"}


# --- T-04 — too short -----------------------------------------------------


def test_change_password_rejects_short(
    client: TestClient, fresh_admin: dict
) -> None:
    _login(client, fresh_admin["username"], fresh_admin["password"])
    r = client.post(
        "/api/auth/change-password",
        json={"new_password": "shortie", "confirm_password": "shortie"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "too_short"


# --- T-05 — passwords don't match ----------------------------------------


def test_change_password_rejects_mismatch(
    client: TestClient, fresh_admin: dict
) -> None:
    _login(client, fresh_admin["username"], fresh_admin["password"])
    r = client.post(
        "/api/auth/change-password",
        json={"new_password": "GoodPassword1!", "confirm_password": "Different2!"},
    )
    assert r.status_code == 400
    assert r.json() == {"detail": "passwords_dont_match"}


# --- T-06 — successful change ---------------------------------------------


def test_change_password_success_clears_must_change_and_audits(
    client: TestClient, settings: Settings, fresh_admin: dict
) -> None:
    _login(client, fresh_admin["username"], fresh_admin["password"])
    r = client.post(
        "/api/auth/change-password",
        json={"new_password": "BrandNew1!", "confirm_password": "BrandNew1!"},
    )
    assert r.status_code == 200
    assert r.json() == {}
    # Fresh cookie issued.
    assert "set-cookie" in r.headers
    assert COOKIE_NAME in r.headers["set-cookie"]

    # /me now reflects must_change_password=False.
    r_me = client.get("/api/auth/me")
    assert r_me.status_code == 200
    assert r_me.json()["must_change_password"] is False

    # Settled routes work now.
    r_logout = client.post("/api/auth/logout")
    assert r_logout.status_code == 200

    # DB row stamped.
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            user = session.query(User).filter_by(username="admin").one()
            assert user.must_change_password == 0
            assert user.password_changed_at is not None
            assert isinstance(user.password_changed_at, datetime)
    finally:
        engine.dispose()

    # login_audit row exists with action='password_changed', success=1.
    rows = _audit_rows(settings)
    pw_changes = [r for r in rows if r.action == "password_changed"]
    assert len(pw_changes) == 1
    assert pw_changes[0].success == 1
    assert pw_changes[0].username == "admin"


# --- T-07 — re-login with new password skips the flow -------------------


def test_relogin_with_new_password_does_not_require_change(
    client: TestClient, fresh_admin: dict
) -> None:
    _login(client, fresh_admin["username"], fresh_admin["password"])
    client.post(
        "/api/auth/change-password",
        json={"new_password": "BrandNew1!", "confirm_password": "BrandNew1!"},
    )
    client.post("/api/auth/logout")
    client.cookies.clear()

    r = _login(client, "admin", "BrandNew1!")
    assert r.status_code == 200
    assert r.json()["user"]["must_change_password"] is False

    # Settled routes work without going through /change-password again.
    r_me = client.get("/api/auth/me")
    assert r_me.status_code == 200


# --- T-09 — WWW-Authenticate header --------------------------------------


def test_409_carries_www_authenticate_change_password(
    client: TestClient, fresh_admin: dict
) -> None:
    _login(client, fresh_admin["username"], fresh_admin["password"])
    r = client.post("/api/auth/logout")
    assert r.status_code == 409
    assert r.headers["www-authenticate"] == "ChangePassword"


# --- T-10 — change-password without auth returns 401, not 409 -----------


def test_change_password_without_cookie_returns_401(client: TestClient) -> None:
    r = client.post(
        "/api/auth/change-password",
        json={"new_password": "BrandNew1!", "confirm_password": "BrandNew1!"},
    )
    assert r.status_code == 401


# --- Settled user can hit change-password too (no-op behaviourally) -----


def test_change_password_works_for_settled_user(
    client: TestClient, settled_user: dict, settings: Settings
) -> None:
    """A user with must_change_password=0 can still change their password —
    /change-password is gated by current_user, not by the settled state.
    Useful for self-service password rotation later.
    """
    _login(client, settled_user["username"], settled_user["password"])
    r = client.post(
        "/api/auth/change-password",
        json={"new_password": "RotatedOK1!", "confirm_password": "RotatedOK1!"},
    )
    assert r.status_code == 200

    # New password works on re-login.
    client.post("/api/auth/logout")
    client.cookies.clear()
    r2 = _login(client, settled_user["username"], "RotatedOK1!")
    assert r2.status_code == 200
    r3 = _login(client, settled_user["username"], settled_user["password"])
    assert r3.status_code == 401


# --- The settled gate applies to /logout (one example of a protected route) -


def test_settled_gate_blocks_protected_routes_except_me_and_change(
    client: TestClient, fresh_admin: dict
) -> None:
    """The two exceptions are /me and /change-password. /logout is gated."""
    _login(client, fresh_admin["username"], fresh_admin["password"])

    # /me — exempt.
    assert client.get("/api/auth/me").status_code == 200
    # /change-password — exempt (it's the way out of the state).
    r_cp = client.post(
        "/api/auth/change-password",
        json={"new_password": "GoodPW01!", "confirm_password": "Different02!"},
    )
    assert r_cp.status_code == 400  # validation error, not 409 — proves the gate didn't fire
    # /logout — gated.
    assert client.post("/api/auth/logout").status_code == 409
