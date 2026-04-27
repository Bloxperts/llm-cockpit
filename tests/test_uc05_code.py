"""UC-05 — code router tests.

Covers test cases T-01..T-10 from docs/specs/test/UC-05-code-page.md.
Most of the chat-related machinery is shared with UC-04 (`stream_reply`,
the conversation CRUD helpers, the `/api/models` picker); this file
focuses on the differences: role gate, default system prompt, mode
isolation, picker filter for `tag=code`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from cockpit.adapters.fake_chat import FakeLLMChat, model_info
from cockpit.adapters.fake_telemetry import FakeTelemetry
from cockpit.config import Settings
from cockpit.db import make_engine, make_session_factory, upgrade_to_head
from cockpit.main import create_app
from cockpit.models import Conversation, ModelTag, Setting, User
from cockpit.ports.llm_chat import ChatChunk
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
            session.add(ModelTag(model="gemma3:27b",     tag="chat", source="auto"))
            session.add(ModelTag(model="qwen3-coder:30b", tag="code", source="auto"))
            session.add(ModelTag(model="phi4:14b",        tag="both", source="auto"))
            session.commit()
    finally:
        engine.dispose()
    return out


def _final_chunk() -> ChatChunk:
    return ChatChunk(
        delta="",
        done=True,
        usage_in=10,
        usage_out=20,
        eval_duration_ns=1_000_000_000,
        prompt_eval_duration_ns=500_000_000,
        total_duration_ns=1_500_000_000,
    )


def _build_client(settings: Settings, *, chat: FakeLLMChat | None = None) -> TestClient:
    chat = chat or FakeLLMChat(
        models=[model_info("qwen3-coder:30b"), model_info("phi4:14b")],
        tokens=["def foo(): return 42"],
        final_chunk=_final_chunk(),
    )
    app = create_app(
        settings,
        chat_factory=lambda url: chat,
        telemetry_factory=lambda: FakeTelemetry(snapshots=[]),
        skip_db_upgrade=True,
        skip_samplers=True,
    )
    app.state._test_chat = chat
    return TestClient(app)


def _login(client: TestClient, seeded: dict, username: str) -> None:
    r = client.post(
        "/api/auth/login",
        json={"username": username, "password": seeded[username]["password"]},
    )
    assert r.status_code == 200, r.text


# =========================================================================
# T-01 / T-02 — create + fetch a code conversation
# =========================================================================


def test_code_user_creates_code_conversation(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        r = client.post("/api/code", json={"model": "qwen3-coder:30b"})
    assert r.status_code == 201
    body = r.json()
    assert body["mode"] == "code"


def test_get_code_conversation_returns_mode_code(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        cid = client.post("/api/code", json={"model": "qwen3-coder:30b"}).json()["conversation_id"]
        r = client.get(f"/api/code/{cid}")
    assert r.status_code == 200
    assert r.json()["mode"] == "code"


# =========================================================================
# T-03 — role gate (chat user → 403 on /api/code/*)
# =========================================================================


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/api/code", {"model": "m"}),
        ("GET", "/api/code", None),
        ("GET", "/api/code/1", None),
        ("PATCH", "/api/code/1", {"title": "x"}),
        ("DELETE", "/api/code/1", None),
        ("POST", "/api/code/1/stream", {"content": "x"}),
        ("POST", "/api/code/1/regenerate", None),
    ],
)
def test_chat_user_blocked_from_code_routes(
    settings: Settings, seeded: dict, method: str, path: str, body: dict | None
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "carol")  # role=chat
        r = client.request(method, path, json=body) if body else client.request(method, path)
    assert r.status_code == 403, (method, path, r.status_code)


def test_admin_user_can_access_code_routes(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")  # role=admin
        r = client.post("/api/code", json={"model": "qwen3-coder:30b"})
    assert r.status_code == 201


# =========================================================================
# T-04 — code conversation absent from /api/chat list (and vice versa)
# =========================================================================


def test_code_and_chat_lists_isolated_by_mode(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")  # role=code → both /chat and /code
        client.post("/api/chat", json={"model": "phi4:14b"})
        client.post("/api/code", json={"model": "qwen3-coder:30b"})

        chat_list = client.get("/api/chat").json()
        code_list = client.get("/api/code").json()
    chat_modes = {c["mode"] for c in chat_list}
    code_modes = {c["mode"] for c in code_list}
    assert chat_modes == {"chat"}
    assert code_modes == {"code"}


# =========================================================================
# T-05 — default system prompt comes from settings row
# =========================================================================


def test_default_system_prompt_from_settings_row(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    custom = "You are a senior Rust dev. Avoid `unsafe`."
    with session_factory() as session:
        session.add(Setting(key="code_default_system_prompt", value=custom))
        session.commit()

    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        cid = client.post("/api/code", json={"model": "m"}).json()["conversation_id"]
        r = client.get(f"/api/code/{cid}")
    assert r.json()["system_prompt"] == custom


# =========================================================================
# T-06 — fallback to bundled default when no settings row
# =========================================================================


def test_default_system_prompt_falls_back_to_bundled_file(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        cid = client.post("/api/code", json={"model": "m"}).json()["conversation_id"]
        r = client.get(f"/api/code/{cid}")
    body = r.json()
    assert body["system_prompt"] is not None
    # Fragment from src/cockpit/default_config/code_default_system_prompt.md
    assert "coding" in body["system_prompt"].lower() or "pair programmer" in body["system_prompt"].lower()


# =========================================================================
# T-07 — picker filter
# =========================================================================


def test_code_picker_excludes_chat_only_models(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        names = {m["name"] for m in client.get("/api/models?tag=code").json()}
    assert "qwen3-coder:30b" in names
    assert "phi4:14b" in names  # both
    assert "gemma3:27b" not in names


# =========================================================================
# T-08 / T-09 — streaming + regenerate
# =========================================================================


def test_code_stream_succeeds(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        cid = client.post("/api/code", json={"model": "qwen3-coder:30b"}).json()["conversation_id"]
        with client.stream(
            "POST", f"/api/code/{cid}/stream", json={"content": "Write fibonacci"}
        ) as r:
            assert r.status_code == 200
            saw_token = False
            saw_done = False
            for line in r.iter_lines():
                norm = line if isinstance(line, str) else line.decode("utf-8")
                if norm.startswith("event:") and "token" in norm:
                    saw_token = True
                if norm.startswith("event:") and "done" in norm:
                    saw_done = True
            assert saw_token
            assert saw_done


def test_code_regenerate_appends_message(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    chat = FakeLLMChat(
        models=[model_info("m")], tokens=["x"], final_chunk=_final_chunk()
    )
    client = _build_client(settings, chat=chat)
    with client:
        _login(client, seeded, "bob")
        cid = client.post("/api/code", json={"model": "m"}).json()["conversation_id"]
        with client.stream("POST", f"/api/code/{cid}/stream", json={"content": "first"}) as r:
            list(r.iter_lines())
        with client.stream("POST", f"/api/code/{cid}/regenerate") as r:
            list(r.iter_lines())

    with session_factory() as session:
        from cockpit.models import Message

        msgs = list(
            session.execute(
                select(Message).where(Message.conversation_id == cid).order_by(Message.id)
            ).scalars()
        )
        roles = [m.role for m in msgs]
        assert roles.count("user") == 2
        assert roles.count("assistant") == 2


# =========================================================================
# T-10 — auth + settled gate
# =========================================================================


def test_code_requires_auth(settings: Settings) -> None:
    client = _build_client(settings)
    with client:
        r = client.get("/api/code")
    assert r.status_code == 401


def test_code_settled_gate(settings: Settings) -> None:
    """Seed a code-role user with must_change_password=1 → 409 on code routes."""
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            session.add(
                User(
                    username="newcoder",
                    pw_hash=hash_password("newCoderPW1!", cost=settings.bcrypt_cost),
                    role="code",
                    must_change_password=1,
                )
            )
            session.commit()
    finally:
        engine.dispose()
    client = _build_client(settings)
    with client:
        client.post(
            "/api/auth/login",
            json={"username": "newcoder", "password": "newCoderPW1!"},
        )
        r = client.get("/api/code")
    assert r.status_code == 409


def test_chat_role_blocked_from_code_picker(settings: Settings, seeded: dict) -> None:
    """The /api/models picker is allowed (settled-gated only) — but UC-05's
    code router is what's gated by role=code. The picker can show code
    models to a chat user too; the routes that act on them are the gate."""
    client = _build_client(settings)
    with client:
        _login(client, seeded, "carol")
        # Picker is open to any settled user.
        r = client.get("/api/models?tag=code")
    assert r.status_code == 200


# =========================================================================
# 404 + 400 path coverage
# =========================================================================


def test_get_404_for_missing_code_conversation(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        assert client.get("/api/code/9999").status_code == 404
        assert client.patch("/api/code/9999", json={"title": "x"}).status_code == 404
        assert client.delete("/api/code/9999").status_code == 404
        assert client.post("/api/code/9999/stream", json={"content": "x"}).status_code == 404
        assert client.post("/api/code/9999/regenerate").status_code == 404


def test_code_regenerate_400_when_no_prior_user(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        cid = client.post("/api/code", json={"model": "m"}).json()["conversation_id"]
        r = client.post(f"/api/code/{cid}/regenerate")
    assert r.status_code == 400


def test_code_patch_persists_changes(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        cid = client.post("/api/code", json={"model": "m"}).json()["conversation_id"]
        r = client.patch(f"/api/code/{cid}", json={"title": "renamed", "model": "phi4:14b"})
    assert r.status_code == 200
    assert r.json()["updated"]["title"] == "renamed"
    assert r.json()["updated"]["model"] == "phi4:14b"


def test_code_delete_returns_204(settings: Settings, seeded: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        cid = client.post("/api/code", json={"model": "m"}).json()["conversation_id"]
        r = client.delete(f"/api/code/{cid}")
    assert r.status_code == 204
