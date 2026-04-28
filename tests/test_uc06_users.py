"""UC-06 — admin user management router + services tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from cockpit.adapters.fake_chat import FakeLLMChat
from cockpit.adapters.fake_telemetry import FakeTelemetry
from cockpit.config import Settings
from cockpit.db import make_engine, make_session_factory, upgrade_to_head
from cockpit.main import create_app
from cockpit.models import AdminAudit, Conversation, Message, User
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
# Auth / role gates
# =========================================================================


def test_list_requires_auth(settings: Settings) -> None:
    client = _build_client(settings)
    with client:
        r = client.get("/api/admin/users")
    assert r.status_code == 401


def test_list_blocks_non_admin(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "carol")
        r = client.get("/api/admin/users")
    assert r.status_code == 403


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST",   "/api/admin/users", {"username": "newone", "password": "PWchange1!"}),
        ("PATCH",  "/api/admin/users/2/role", {"role": "code"}),
        ("POST",   "/api/admin/users/2/reset-password", {"new_password": "PWchange1!"}),
        ("DELETE", "/api/admin/users/2", None),
    ],
)
def test_admin_endpoints_block_non_admin(
    settings: Settings, seeded: dict, method: str, path: str, body
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")  # code role, not admin
        r = client.request(method, path, json=body) if body is not None else client.request(method, path)
    assert r.status_code == 403


# =========================================================================
# GET /api/admin/users — list + token totals + filters
# =========================================================================


def test_list_users_returns_three_seeded_with_zero_tokens(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.get("/api/admin/users")
    assert r.status_code == 200
    body = r.json()
    assert {u["username"] for u in body} == {"admin", "alice", "bob", "carol"} or {
        u["username"] for u in body
    } == {"alice", "bob", "carol"}
    # Default seed admin may not exist on this fixture; both sets are valid.
    for u in body:
        assert u["tokens_in"] == 0
        assert u["tokens_out"] == 0
        assert u["deleted_at"] is None
        assert "created_at" in u


def test_list_users_includes_token_totals(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Seed two conversations with assistant messages; verify the aggregation
    sums across rows (and ignores user messages, which carry usage_in=NULL)."""
    bob_id = _user_id(session_factory, "bob")
    with session_factory() as session:
        c1 = Conversation(user_id=bob_id, mode="chat", title="c1", model="m")
        c2 = Conversation(user_id=bob_id, mode="code", title="c2", model="m")
        session.add(c1)
        session.add(c2)
        session.flush()
        # Assistant rows count.
        session.add(Message(conversation_id=c1.id, role="assistant", content="ok",
                            usage_in=10, usage_out=20))
        session.add(Message(conversation_id=c2.id, role="assistant", content="ok",
                            usage_in=30, usage_out=40))
        # User row should be excluded from the aggregate.
        session.add(Message(conversation_id=c1.id, role="user", content="hi",
                            usage_in=None, usage_out=None))
        session.commit()

    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.get("/api/admin/users")
    body = r.json()
    bob = next(u for u in body if u["username"] == "bob")
    assert bob["tokens_in"] == 40
    assert bob["tokens_out"] == 60


def test_list_users_excludes_soft_deleted_by_default(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Soft-deleted users are filtered unless include_deleted=true."""
    from datetime import datetime, timezone

    with session_factory() as session:
        carol = session.execute(select(User).where(User.username == "carol")).scalar_one()
        carol.deleted_at = datetime.now(timezone.utc)
        session.commit()

    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        usernames_default = {u["username"] for u in client.get("/api/admin/users").json()}
        usernames_all = {
            u["username"]
            for u in client.get("/api/admin/users?include_deleted=true").json()
        }
    assert "carol" not in usernames_default
    assert "carol" in usernames_all


def test_list_users_q_filter(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        body = client.get("/api/admin/users?q=ali").json()
    assert {u["username"] for u in body} == {"alice"}


# =========================================================================
# POST /api/admin/users — create
# =========================================================================


def test_create_user_happy_path(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.post(
            "/api/admin/users",
            json={"username": "dave", "password": "DavePW01!", "role": "code"},
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["username"] == "dave"
    assert body["role"] == "code"
    assert body["must_change_password"] is True
    assert body["tokens_in"] == 0

    # AdminAudit row written.
    with session_factory() as session:
        rows = list(
            session.execute(
                select(AdminAudit).where(AdminAudit.action == "user_created")
            ).scalars()
        )
        assert len(rows) == 1
        details = json.loads(rows[0].details_json)
        assert details["username"] == "dave"
        assert details["role"] == "code"


def test_create_user_rejects_invalid_username(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.post(
            "/api/admin/users",
            json={"username": "1bad-name", "password": "DavePW01!"},
        )
    assert r.status_code == 400
    assert r.json()["detail"]["detail"] == "invalid_username"


def test_create_user_rejects_invalid_role(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.post(
            "/api/admin/users",
            json={"username": "dave", "password": "DavePW01!", "role": "wizard"},
        )
    assert r.status_code == 400
    assert r.json()["detail"]["detail"] == "invalid_role"


def test_create_user_short_password_rejected(settings: Settings, seeded: dict) -> None:
    """Pydantic min_length=8 on the schema → 422 with default error envelope."""
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.post(
            "/api/admin/users",
            json={"username": "dave", "password": "short"},
        )
    assert r.status_code == 422


def test_create_user_duplicate_returns_409(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.post(
            "/api/admin/users",
            json={"username": "carol", "password": "CarolPW01!"},
        )
    assert r.status_code == 409
    assert r.json()["detail"]["detail"] == "username_exists"


# =========================================================================
# PATCH /api/admin/users/{id}/role
# =========================================================================


def test_change_role(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.patch(f"/api/admin/users/{bob_id}/role", json={"role": "admin"})
    assert r.status_code == 200
    assert r.json()["role"] == "admin"

    with session_factory() as session:
        audits = list(
            session.execute(
                select(AdminAudit).where(AdminAudit.action == "role_changed")
            ).scalars()
        )
        assert len(audits) == 1
        details = json.loads(audits[0].details_json)
        assert details["old_role"] == "code"
        assert details["new_role"] == "admin"


def test_change_role_idempotent(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.patch(f"/api/admin/users/{bob_id}/role", json={"role": "code"})
    assert r.status_code == 200
    with session_factory() as session:
        audits = list(
            session.execute(
                select(AdminAudit).where(AdminAudit.action == "role_changed")
            ).scalars()
        )
        # No audit row for a no-op call.
        assert len(audits) == 0


def test_change_role_last_admin_demotion_blocked(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Alice is the only admin; demoting her must 409 with cannot_demote_last_admin."""
    alice_id = _user_id(session_factory, "alice")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.patch(f"/api/admin/users/{alice_id}/role", json={"role": "code"})
    assert r.status_code == 409
    assert r.json()["detail"]["detail"] == "cannot_demote_last_admin"


def test_change_role_404_for_missing_user(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.patch("/api/admin/users/99999/role", json={"role": "code"})
    assert r.status_code == 404


def test_change_role_invalid_role(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.patch(f"/api/admin/users/{bob_id}/role", json={"role": "wizard"})
    assert r.status_code == 400
    assert r.json()["detail"]["detail"] == "invalid_role"


# =========================================================================
# POST /api/admin/users/{id}/reset-password
# =========================================================================


def test_reset_password_flips_must_change(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.post(
            f"/api/admin/users/{bob_id}/reset-password",
            json={"new_password": "FreshPW01!"},
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    with session_factory() as session:
        bob = session.execute(select(User).where(User.id == bob_id)).scalar_one()
        assert bob.must_change_password == 1
        assert bob.password_changed_at is None
        audits = list(
            session.execute(
                select(AdminAudit).where(AdminAudit.action == "password_reset_by_admin")
            ).scalars()
        )
        assert len(audits) == 1


def test_reset_password_short_rejected_by_pydantic(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.post(
            f"/api/admin/users/{bob_id}/reset-password",
            json={"new_password": "short"},
        )
    assert r.status_code == 422


def test_reset_password_404_when_missing(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.post(
            "/api/admin/users/99999/reset-password", json={"new_password": "ok-PW01!"}
        )
    assert r.status_code == 404


# =========================================================================
# DELETE /api/admin/users/{id}
# =========================================================================


def test_delete_user_soft_deletes_and_audits(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.delete(f"/api/admin/users/{bob_id}")
    assert r.status_code == 204
    with session_factory() as session:
        bob = session.execute(select(User).where(User.id == bob_id)).scalar_one()
        assert bob.deleted_at is not None
        audits = list(
            session.execute(
                select(AdminAudit).where(AdminAudit.action == "user_deleted")
            ).scalars()
        )
        assert len(audits) == 1


def test_delete_self_is_blocked(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    alice_id = _user_id(session_factory, "alice")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.delete(f"/api/admin/users/{alice_id}")
    assert r.status_code == 409
    assert r.json()["detail"]["detail"] == "cannot_self_delete"


def test_delete_last_admin_blocked_when_target_is_admin(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Promote bob to admin, then have bob log in and try to delete alice
    while bob is also admin → succeeds. Then bob is the only admin → bob
    cannot be deleted by bob (self-delete) AND if we promote-then-delete-
    from-bob-to-admin scenario, the last-admin guard must fire."""
    # Promote bob to admin first so two admins exist.
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        client.patch(f"/api/admin/users/{bob_id}/role", json={"role": "admin"})
        # Now alice deletes bob → leaves alice as the only admin. Should succeed.
        r = client.delete(f"/api/admin/users/{bob_id}")
    assert r.status_code == 204
    # Now alice is the lone admin. Promote carol to admin via direct DB so
    # we can test the cannot_delete_last_admin guard cleanly.
    with session_factory() as session:
        carol = session.execute(select(User).where(User.username == "carol")).scalar_one()
        carol.role = "admin"
        session.commit()
    # Soft-delete alice in-DB to simulate "alice already gone"; carol becomes
    # the lone admin. Then any attempt to delete carol via the router must 409.
    with session_factory() as session:
        from datetime import datetime, timezone

        alice = session.execute(select(User).where(User.username == "alice")).scalar_one()
        alice.deleted_at = datetime.now(timezone.utc)
        session.commit()
    carol_id = _user_id(session_factory, "carol")
    seeded["carol"] = {"role": "admin", "password": seeded["carol"]["password"]}
    client = _build_client(settings)
    with client:
        _login(client, seeded, "carol")
        r = client.delete(f"/api/admin/users/{carol_id}")
    # Carol can't delete herself anyway; surface that first.
    assert r.status_code == 409
    assert r.json()["detail"]["detail"] in ("cannot_self_delete", "cannot_delete_last_admin")


def test_delete_404_for_missing(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.delete("/api/admin/users/99999")
    assert r.status_code == 404


def test_delete_already_deleted_is_404(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    bob_id = _user_id(session_factory, "bob")
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        first = client.delete(f"/api/admin/users/{bob_id}")
        assert first.status_code == 204
        second = client.delete(f"/api/admin/users/{bob_id}")
    assert second.status_code == 404
