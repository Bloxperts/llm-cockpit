"""UC-01 — auth router tests.

Maps to UC-01 Test Spec T-01..T-12 plus the role discrepancy follow-up.
We don't reach a real Ollama; FakeLLMChat is injected for the startup probe.

Fixture seeds three users via `cockpit-admin user-add`-style direct inserts
through `services/users.seed_admin`. Roles cover every rung of the ladder
(chat / code / admin) per ADR-004.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy.orm import Session

from cockpit.adapters.fake_chat import FakeLLMChat
from cockpit.config import Settings
from cockpit.db import make_engine, make_session_factory, upgrade_to_head
from cockpit.main import create_app
from cockpit.models import LoginAudit, User
from cockpit.routers.auth import (
    COOKIE_NAME,
    JWT_ALG,
    SLIDING_RENEWAL_THRESHOLD,
    _create_token,
)
from cockpit.services.users import hash_password


# --- Fixtures --------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "cockpit-data"
    data_dir.mkdir()
    s = Settings(
        data_dir=data_dir,
        host="127.0.0.1",
        port=18080,
        # Fast bcrypt for tests so the suite stays under a second.
        bcrypt_cost=4,
        # Fixed JWT secret so tests can mint tokens manually.
        jwt_secret="test-secret-do-not-use-in-prod",
    )
    upgrade_to_head(s.db_url)
    return s


@pytest.fixture
def seeded_users(settings: Settings) -> dict[str, dict]:
    """Three users, one per role on the ladder."""
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    pw_admin = "PWadmin1!"
    pw_code = "PWcode01!"
    pw_chat = "PWchat01!"
    try:
        with factory() as session:
            session.add(
                User(
                    username="alice",
                    pw_hash=hash_password(pw_admin, cost=settings.bcrypt_cost),
                    role="admin",
                    must_change_password=0,
                )
            )
            session.add(
                User(
                    username="bob",
                    pw_hash=hash_password(pw_code, cost=settings.bcrypt_cost),
                    role="code",
                    must_change_password=0,
                )
            )
            session.add(
                User(
                    username="carol",
                    pw_hash=hash_password(pw_chat, cost=settings.bcrypt_cost),
                    role="chat",
                    must_change_password=0,
                )
            )
            session.commit()
    finally:
        engine.dispose()
    return {
        "alice": {"role": "admin", "password": pw_admin},
        "bob": {"role": "code", "password": pw_code},
        "carol": {"role": "chat", "password": pw_chat},
    }


@pytest.fixture
def client(settings: Settings, seeded_users: dict) -> TestClient:
    app = create_app(
        settings,
        chat_factory=lambda url: FakeLLMChat(models=[]),
        skip_db_upgrade=True,  # already migrated by the settings fixture
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


# --- T-02 / T-04 / T-05 / T-09 / T-12 -------------------------------------


def test_login_success_sets_cookie(
    client: TestClient, seeded_users: dict
) -> None:
    r = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": seeded_users["alice"]["password"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["username"] == "alice"
    assert body["user"]["role"] == "admin"
    assert body["user"]["must_change_password"] is False
    assert body["ttl_seconds"] == 7 * 86400

    # Cookie shape: HttpOnly, SameSite=Strict, Path=/.
    set_cookie = r.headers["set-cookie"]
    assert COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie.lower() or "samesite=strict" in set_cookie.lower()
    assert "Path=/" in set_cookie
    assert "Secure" not in set_cookie  # LAN-only HTTP per ADR-003


def test_each_role_can_log_in(client: TestClient, seeded_users: dict) -> None:
    for username, info in seeded_users.items():
        client.cookies.clear()
        r = client.post(
            "/api/auth/login",
            json={"username": username, "password": info["password"]},
        )
        assert r.status_code == 200, (username, r.text)
        assert r.json()["user"]["role"] == info["role"]


# --- T-03 / T-04 -----------------------------------------------------------


def test_wrong_password_returns_401_invalid_credentials(
    client: TestClient,
) -> None:
    r = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "wrong-password"},
    )
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid credentials"}


def test_unknown_username_returns_401_same_body(client: TestClient) -> None:
    r = client.post(
        "/api/auth/login",
        json={"username": "no-such-user", "password": "whatever"},
    )
    assert r.status_code == 401
    # Identical body — no info leak about which field was wrong.
    assert r.json() == {"detail": "Invalid credentials"}


# --- T-05 / T-06 lockout --------------------------------------------------


def test_lockout_after_five_failures(
    client: TestClient, seeded_users: dict
) -> None:
    """5 fails per username per 5 min → 6th is 429 with retry_after_seconds=60."""
    for _ in range(5):
        r = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "nope"},
        )
        assert r.status_code == 401

    r = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": seeded_users["alice"]["password"]},
    )
    assert r.status_code == 429
    body = r.json()
    assert body["detail"]["detail"] == "too_many_attempts"
    assert body["detail"]["retry_after_seconds"] > 0
    assert body["detail"]["retry_after_seconds"] <= 60
    assert "retry-after" in {h.lower(): None for h in r.headers}


def test_lockout_is_per_username(
    client: TestClient, seeded_users: dict
) -> None:
    """Five fails on alice does NOT lock out bob."""
    for _ in range(5):
        client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "nope"},
        )
    r = client.post(
        "/api/auth/login",
        json={"username": "bob", "password": seeded_users["bob"]["password"]},
    )
    assert r.status_code == 200


def test_successful_login_resets_failure_counter(
    client: TestClient, seeded_users: dict
) -> None:
    """Successful login clears the failure counter; subsequent fails start
    fresh and don't trigger lockout immediately.
    """
    for _ in range(4):
        client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "nope"},
        )
    r = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": seeded_users["alice"]["password"]},
    )
    assert r.status_code == 200
    client.cookies.clear()
    # After success, four more fails should NOT yet trigger 429.
    for _ in range(4):
        r = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "nope"},
        )
        assert r.status_code == 401  # still 401, not 429


# --- T-09 me -------------------------------------------------------------


def test_me_without_cookie_returns_401(client: TestClient) -> None:
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_with_valid_cookie_returns_user(
    client: TestClient, seeded_users: dict
) -> None:
    client.post(
        "/api/auth/login",
        json={"username": "alice", "password": seeded_users["alice"]["password"]},
    )
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "alice"
    assert body["role"] == "admin"
    assert body["must_change_password"] is False
    assert "id" in body


def test_me_with_garbage_cookie_returns_401(client: TestClient) -> None:
    client.cookies.set(COOKIE_NAME, "this-is-not-a-jwt")
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_with_expired_cookie_returns_401(
    client: TestClient, settings: Settings, seeded_users: dict
) -> None:
    """Forge a token whose exp is in the past; jose raises ExpiredSignature."""
    expired_token = jwt.encode(
        {"sub": "1", "exp": int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp())},
        settings.jwt_secret,
        algorithm=JWT_ALG,
    )
    client.cookies.set(COOKIE_NAME, expired_token)
    r = client.get("/api/auth/me")
    assert r.status_code == 401


# --- T-10 logout ---------------------------------------------------------


def test_logout_clears_cookie(client: TestClient, seeded_users: dict) -> None:
    client.post(
        "/api/auth/login",
        json={"username": "alice", "password": seeded_users["alice"]["password"]},
    )
    r_logout = client.post("/api/auth/logout")
    assert r_logout.status_code == 200

    # After logout, the cookie is cleared. TestClient honours Set-Cookie with
    # max-age=0 / expired date by removing the cookie; subsequent /me is 401.
    client.cookies.clear()  # belt + braces
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_logout_without_cookie_returns_401(client: TestClient) -> None:
    """Logout is gated by current_user_must_be_settled (UC-09); without a
    cookie the user is unauthenticated. The browser swallows the response
    and redirects anyway — see frontend_dist/dashboard/index.html.
    """
    r = client.post("/api/auth/logout")
    assert r.status_code == 401


# --- T-11 audit ---------------------------------------------------------


def test_login_audit_row_per_attempt(
    client: TestClient, settings: Settings, seeded_users: dict
) -> None:
    # Three successes + three fails + one logout = seven audit rows.
    client.post("/api/auth/login", json={"username": "alice", "password": seeded_users["alice"]["password"]})
    client.cookies.clear()
    client.post("/api/auth/login", json={"username": "bob", "password": seeded_users["bob"]["password"]})
    client.cookies.clear()
    client.post("/api/auth/login", json={"username": "carol", "password": seeded_users["carol"]["password"]})
    client.cookies.clear()
    for _ in range(3):
        client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
    client.post("/api/auth/login", json={"username": "alice", "password": seeded_users["alice"]["password"]})
    client.post("/api/auth/logout")

    rows = _audit_rows(settings)
    success_logins = [r for r in rows if r.action == "login" and r.success == 1]
    fail_logins = [r for r in rows if r.action == "login" and r.success == 0]
    logouts = [r for r in rows if r.action == "logout"]

    assert len(success_logins) == 4
    assert len(fail_logins) == 3
    assert len(logouts) == 1

    for row in rows:
        assert row.ts is not None
        assert row.action in ("login", "logout")


# --- T-12 role gates ----------------------------------------------------


def test_require_role_gates_correctly(
    settings: Settings, seeded_users: dict
) -> None:
    """Sanity-check require_role at the ladder boundaries.

    Build a side-app *without* the StaticFiles mount: the mount at `/` is a
    catch-all that pre-empts routes added after it, so we register the test
    endpoints on a clean app that includes the auth router but not the
    frontend mount. Same DB and same Settings via app.state.
    """
    from fastapi import Depends, FastAPI

    from cockpit.db import make_engine, make_session_factory
    from cockpit.routers import auth as auth_router
    from cockpit.routers.auth import require_role

    side_app = FastAPI()
    side_app.state.settings = settings
    engine = make_engine(settings.db_url)
    side_app.state.engine = engine
    side_app.state.session_factory = make_session_factory(engine)
    side_app.state.rate_limiter = auth_router.RateLimiter()
    side_app.include_router(auth_router.router, prefix="/api/auth")

    @side_app.get("/api/_test/chat")
    def _chat_only(_user=Depends(require_role("chat"))):
        return {"ok": True}

    @side_app.get("/api/_test/code")
    def _code_only(_user=Depends(require_role("code"))):
        return {"ok": True}

    @side_app.get("/api/_test/admin")
    def _admin_only(_user=Depends(require_role("admin"))):
        return {"ok": True}

    with TestClient(side_app) as tc:
        # Carol (chat) → can access /chat, blocked from /code and /admin.
        tc.post(
            "/api/auth/login",
            json={"username": "carol", "password": seeded_users["carol"]["password"]},
        )
        assert tc.get("/api/_test/chat").status_code == 200
        assert tc.get("/api/_test/code").status_code == 403
        assert tc.get("/api/_test/admin").status_code == 403

        # Bob (code) → can access /chat and /code; blocked from /admin.
        tc.cookies.clear()
        tc.post(
            "/api/auth/login",
            json={"username": "bob", "password": seeded_users["bob"]["password"]},
        )
        assert tc.get("/api/_test/chat").status_code == 200
        assert tc.get("/api/_test/code").status_code == 200
        assert tc.get("/api/_test/admin").status_code == 403

        # Alice (admin) → can access all three.
        tc.cookies.clear()
        tc.post(
            "/api/auth/login",
            json={"username": "alice", "password": seeded_users["alice"]["password"]},
        )
        assert tc.get("/api/_test/chat").status_code == 200
        assert tc.get("/api/_test/code").status_code == 200
        assert tc.get("/api/_test/admin").status_code == 200
    engine.dispose()


def test_role_flip_takes_effect_on_next_request(
    client: TestClient, settings: Settings, seeded_users: dict
) -> None:
    """ADR-004 §5: role is resolved per-request from the DB, not baked into
    JWT claims. An admin demotion should bite on the very next request,
    even with an unchanged cookie.
    """
    # Alice logs in as admin, sees role=admin.
    r = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": seeded_users["alice"]["password"]},
    )
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "admin"

    # Demote Alice to chat in the DB directly (no admin endpoint exists yet).
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            alice = session.query(User).filter_by(username="alice").one()
            alice.role = "chat"
            session.commit()
    finally:
        engine.dispose()

    # Same cookie, /me now reflects chat.
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["role"] == "chat"


def test_jwt_carries_only_sub_tkv_exp_no_role(
    settings: Settings, seeded_users: dict
) -> None:
    """The JWT payload contains `sub`, `tkv`, and `exp`, nothing else.

    Sprint 7 added `tkv` (token_version) to the claim set so the server
    can revoke all outstanding tokens by bumping a counter. The claim
    list deliberately stays minimal — no role, no permissions, no
    user-supplied metadata that could be tampered with.
    """
    token = _create_token(
        user_id=42, ttl_seconds=600, secret=settings.jwt_secret, token_version=3
    )
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALG])
    assert payload["sub"] == "42"
    assert payload["tkv"] == 3
    assert "exp" in payload
    assert "role" not in payload
    assert set(payload.keys()) == {"sub", "tkv", "exp"}


# --- F5 sliding renewal --------------------------------------------------


def test_sliding_renewal_refreshes_near_expiry(
    settings: Settings, seeded_users: dict
) -> None:
    """A token with < 1 day left causes /me to set a fresh cookie."""
    app = create_app(
        settings,
        chat_factory=lambda url: FakeLLMChat(models=[]),
        skip_db_upgrade=True,
    )
    with TestClient(app) as tc:
        # Look up alice's id.
        engine = make_engine(settings.db_url)
        factory = make_session_factory(engine)
        try:
            with factory() as session:
                alice_id = session.query(User).filter_by(username="alice").one().id
        finally:
            engine.dispose()

        # Mint a token that expires inside the renewal window (5 minutes).
        short_ttl = int(SLIDING_RENEWAL_THRESHOLD.total_seconds()) // 2
        soon_token = _create_token(alice_id, short_ttl, settings.jwt_secret)
        tc.cookies.set(COOKIE_NAME, soon_token)

        r = tc.get("/api/auth/me")
        assert r.status_code == 200
        # Response should carry a Set-Cookie that refreshes cockpit_jwt.
        assert "set-cookie" in r.headers
        assert COOKIE_NAME in r.headers["set-cookie"]


def test_sliding_renewal_does_not_fire_when_token_is_fresh(
    settings: Settings, seeded_users: dict
) -> None:
    """A token issued moments ago (7 days from expiry) should NOT trigger
    a refresh — sliding is only inside the last day window.
    """
    app = create_app(
        settings,
        chat_factory=lambda url: FakeLLMChat(models=[]),
        skip_db_upgrade=True,
    )
    with TestClient(app) as tc:
        r = tc.post(
            "/api/auth/login",
            json={"username": "alice", "password": seeded_users["alice"]["password"]},
        )
        assert r.status_code == 200
        # The /me response right after login should NOT carry another Set-Cookie
        # (token has 7 days left; renewal window is < 1 day).
        r2 = tc.get("/api/auth/me")
        assert r2.status_code == 200
        assert "set-cookie" not in r2.headers


# --- ChangePasswordRequest schema sanity (UC-09 will use it) -------------


def test_login_request_schema_validates_required_fields() -> None:
    from cockpit.schemas import LoginRequest
    from pydantic import ValidationError

    LoginRequest(username="alice", password="x")
    with pytest.raises(ValidationError):
        LoginRequest(username="", password="x")  # min_length=1
    with pytest.raises(ValidationError):
        LoginRequest(username="alice", password="")  # min_length=1


# --- Admin user with deleted_at is not authenticatable -------------------


def test_soft_deleted_user_cannot_log_in(
    settings: Settings, seeded_users: dict
) -> None:
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            alice = session.query(User).filter_by(username="alice").one()
            alice.deleted_at = datetime.now(timezone.utc)
            session.commit()
    finally:
        engine.dispose()

    app = create_app(settings, chat_factory=lambda url: FakeLLMChat(models=[]), skip_db_upgrade=True)
    with TestClient(app) as tc:
        r = tc.post(
            "/api/auth/login",
            json={"username": "alice", "password": seeded_users["alice"]["password"]},
        )
        assert r.status_code == 401


def test_soft_deleted_user_with_existing_token_loses_access(
    settings: Settings, seeded_users: dict
) -> None:
    """Cookie is valid JWT-wise but user.deleted_at is set → 401."""
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            alice = session.query(User).filter_by(username="alice").one()
            alice_id = alice.id
        # log in alice while she's still active
        app = create_app(settings, chat_factory=lambda url: FakeLLMChat(models=[]), skip_db_upgrade=True)
        with TestClient(app) as tc:
            r = tc.post(
                "/api/auth/login",
                json={"username": "alice", "password": seeded_users["alice"]["password"]},
            )
            assert r.status_code == 200

            # now soft-delete alice
            with factory() as session2:
                alice2 = session2.query(User).filter_by(id=alice_id).one()
                alice2.deleted_at = datetime.now(timezone.utc)
                session2.commit()

            r2 = tc.get("/api/auth/me")
            assert r2.status_code == 401
    finally:
        engine.dispose()


# Helper imports must be lazy where they would create circulars at fixture time.
_ = Session
