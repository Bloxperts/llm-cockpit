"""Sprint 9 — UC-10 Admin Ollama configuration tests.

Covers the four endpoint groups added in this sprint:

    PATCH/DELETE /api/admin/ollama/models/{model}/tag
    GET/PUT      /api/admin/ollama/settings
    GET          /api/admin/ollama/metrics
    GET          /api/admin/ollama/metrics/{model}
    GET          /api/admin/audit + /api/admin/audit/export

Plus the `services.model_tags.reapply_heuristics` helper that the
PUT settings handler and the `ModelStateSampler` lean on.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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
from cockpit.models import (
    AdminAudit,
    Conversation,
    LoginAudit,
    Message,
    ModelTag,
    Setting,
    User,
)
from cockpit.ports.llm_chat import ModelInfo
from cockpit.services.model_tags import (
    SETTINGS_KEY_TAG_HEURISTICS,
    reapply_heuristics,
)
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
def session_factory(settings: Settings) -> sessionmaker:
    engine = make_engine(settings.db_url)
    return make_session_factory(engine)


@pytest.fixture
def seeded(settings: Settings) -> dict:
    """Three users — one of each role — with passwords known to the tests."""
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


def _model_info(name: str, size: int = 4_500_000_000) -> ModelInfo:
    return ModelInfo(
        name=name,
        size_bytes=size,
        modified=datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
        digest="sha256:" + name + "-fake",
    )


def _build_client(settings: Settings, *, models: list[ModelInfo] | None = None) -> TestClient:
    app = create_app(
        settings,
        chat_factory=lambda url: FakeLLMChat(models=models or []),
        telemetry_factory=lambda: FakeTelemetry(snapshots=[]),
        skip_db_upgrade=True,
        skip_samplers=True,
    )
    # Tests bypass the live samplers — prime the cached model state directly
    # so the heuristic re-application has data to work with.
    app.state.model_state.available_models = list(models or [])
    return TestClient(app)


def _login(client: TestClient, seeded: dict, username: str) -> None:
    r = client.post(
        "/api/auth/login",
        json={"username": username, "password": seeded[username]["password"]},
    )
    assert r.status_code == 200, r.text


# =========================================================================
# Auth gate
# =========================================================================


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/admin/ollama/settings", None),
        ("PUT", "/api/admin/ollama/settings", {"code_default_system_prompt": "x"}),
        ("PATCH", "/api/admin/ollama/models/llama3:8b/tag", {"tag": "chat"}),
        ("DELETE", "/api/admin/ollama/models/llama3:8b/tag", None),
        ("GET", "/api/admin/ollama/metrics", None),
        ("GET", "/api/admin/ollama/metrics/llama3:8b", None),
        ("GET", "/api/admin/audit", None),
        ("GET", "/api/admin/audit/export", None),
    ],
)
def test_admin_ollama_routes_reject_chat_user(
    settings: Settings,
    seeded: dict,
    method: str,
    path: str,
    body: dict | None,
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "carol")  # chat role
        r = client.request(method, path, json=body)
    assert r.status_code in (401, 403)


def test_admin_ollama_routes_reject_code_user(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "bob")
        r = client.get("/api/admin/ollama/settings")
    assert r.status_code in (401, 403)


def test_admin_ollama_routes_reject_unauthenticated(
    settings: Settings,
) -> None:
    client = _build_client(settings)
    with client:
        r = client.get("/api/admin/ollama/settings")
    assert r.status_code == 401


# =========================================================================
# Tag CRUD
# =========================================================================


def test_patch_tag_creates_override_row(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.patch(
            "/api/admin/ollama/models/llama3:8b/tag", json={"tag": "code"}
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"model": "llama3:8b", "tag": "code", "source": "override"}
    with session_factory() as session:
        row = session.execute(
            select(ModelTag).where(ModelTag.model == "llama3:8b")
        ).scalar_one()
    assert row.source == "override"
    assert row.tag == "code"


def test_patch_tag_writes_audit(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        client.patch(
            "/api/admin/ollama/models/coder-7b/tag", json={"tag": "code"}
        )
    with session_factory() as session:
        rows = session.execute(
            select(AdminAudit).where(AdminAudit.action == "model_tag_set")
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].target_model == "coder-7b"
    assert json.loads(rows[0].details_json or "{}") == {"tag": "code"}


def test_patch_tag_idempotent(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        client.patch(
            "/api/admin/ollama/models/llama3:8b/tag", json={"tag": "chat"}
        )
        client.patch(
            "/api/admin/ollama/models/llama3:8b/tag", json={"tag": "chat"}
        )
    with session_factory() as session:
        tag_rows = session.execute(
            select(ModelTag).where(ModelTag.model == "llama3:8b")
        ).scalars().all()
        audit_rows = session.execute(
            select(AdminAudit).where(AdminAudit.action == "model_tag_set")
        ).scalars().all()
    assert len(tag_rows) == 1
    assert len(audit_rows) == 2  # one per call


def test_patch_tag_rejects_invalid_tag(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.patch(
            "/api/admin/ollama/models/llama3:8b/tag", json={"tag": "nope"}
        )
    assert r.status_code == 422


def test_delete_tag_removes_override_and_reapplies_heuristic(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Override 'code', then DELETE — should fall back to whatever the
    heuristic decides for the name. 'llama3:8b' doesn't match any code
    pattern, so the auto tag should be 'chat'."""
    client = _build_client(settings, models=[_model_info("llama3:8b")])
    with client:
        _login(client, seeded, "alice")
        client.patch(
            "/api/admin/ollama/models/llama3:8b/tag", json={"tag": "code"}
        )
        r = client.delete("/api/admin/ollama/models/llama3:8b/tag")
    assert r.status_code == 204
    with session_factory() as session:
        row = session.execute(
            select(ModelTag).where(ModelTag.model == "llama3:8b")
        ).scalar_one()
    assert row.source == "auto"
    assert row.tag == "chat"


def test_delete_tag_writes_audit(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    client = _build_client(settings, models=[_model_info("llama3:8b")])
    with client:
        _login(client, seeded, "alice")
        client.patch(
            "/api/admin/ollama/models/llama3:8b/tag", json={"tag": "code"}
        )
        client.delete("/api/admin/ollama/models/llama3:8b/tag")
    with session_factory() as session:
        rows = session.execute(
            select(AdminAudit).where(AdminAudit.action == "model_tag_cleared")
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].target_model == "llama3:8b"


def test_delete_tag_no_existing_override_is_idempotent(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.delete("/api/admin/ollama/models/never-existed/tag")
    assert r.status_code == 204
    with session_factory() as session:
        rows = session.execute(
            select(AdminAudit).where(AdminAudit.action == "model_tag_cleared")
        ).scalars().all()
    assert rows == []


def test_delete_tag_unknown_model_returns_204(
    settings: Settings, seeded: dict
) -> None:
    """Endpoint is idempotent on names not currently served by Ollama."""
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.delete("/api/admin/ollama/models/no-such-model/tag")
    assert r.status_code == 204


# =========================================================================
# Settings GET / PUT
# =========================================================================


def test_get_settings_returns_nulls_when_unset(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.get("/api/admin/ollama/settings")
    assert r.status_code == 200
    assert r.json() == {
        "code_default_system_prompt": None,
        "tag_heuristics_yaml": None,
    }


def test_put_settings_writes_both_keys(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.put(
            "/api/admin/ollama/settings",
            json={
                "code_default_system_prompt": "Be terse.",
                "tag_heuristics_yaml": "code_patterns:\n  - 'coder'\n",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert sorted(body["updated"]) == sorted(
        ["code_default_system_prompt", "tag_heuristics_yaml"]
    )
    with session_factory() as session:
        rows = {
            r.key: r.value
            for r in session.execute(select(Setting)).scalars()
        }
    assert rows["code_default_system_prompt"] == "Be terse."
    assert "coder" in rows["tag_heuristics_yaml"]


def test_put_settings_partial_body_only_writes_supplied_keys(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """First PUT both, then PUT one — the other should remain untouched."""
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        client.put(
            "/api/admin/ollama/settings",
            json={
                "code_default_system_prompt": "Be terse.",
                "tag_heuristics_yaml": "code_patterns:\n  - 'coder'\n",
            },
        )
        r = client.put(
            "/api/admin/ollama/settings",
            json={"code_default_system_prompt": "Be very terse."},
        )
    assert r.status_code == 200
    assert r.json()["updated"] == ["code_default_system_prompt"]
    with session_factory() as session:
        rows = {
            r.key: r.value
            for r in session.execute(select(Setting)).scalars()
        }
    assert rows["code_default_system_prompt"] == "Be very terse."
    assert "coder" in rows["tag_heuristics_yaml"]  # preserved


def test_put_settings_invalid_yaml_returns_400(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Malformed YAML rejects with 400 before any DB write."""
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        # Pre-seed a valid row to confirm it's preserved on failure.
        client.put(
            "/api/admin/ollama/settings",
            json={"tag_heuristics_yaml": "code_patterns:\n  - 'coder'\n"},
        )
        r = client.put(
            "/api/admin/ollama/settings",
            json={"tag_heuristics_yaml": "code_patterns: [unclosed"},
        )
    assert r.status_code == 400
    assert r.json()["detail"]["detail"] == "invalid_yaml"
    with session_factory() as session:
        row = session.execute(
            select(Setting).where(Setting.key == SETTINGS_KEY_TAG_HEURISTICS)
        ).scalar_one()
    assert "coder" in (row.value or "")  # preserved


def test_put_settings_writes_audit(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        client.put(
            "/api/admin/ollama/settings",
            json={"code_default_system_prompt": "anything"},
        )
    with session_factory() as session:
        rows = session.execute(
            select(AdminAudit).where(AdminAudit.action == "settings_updated")
        ).scalars().all()
    assert len(rows) == 1
    details = json.loads(rows[0].details_json or "{}")
    assert details == {"keys_changed": ["code_default_system_prompt"]}


def test_put_settings_yaml_change_reapplies_heuristics(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Saving a new YAML body re-evaluates auto-tagged rows immediately."""
    models = [_model_info("llama3:8b"), _model_info("coder-7b")]
    client = _build_client(settings, models=models)
    with client:
        _login(client, seeded, "alice")
        # Initial PUT with a YAML that doesn't match either name → both 'chat'.
        client.put(
            "/api/admin/ollama/settings",
            json={"tag_heuristics_yaml": "code_patterns:\n  - 'never-matches'\n"},
        )
        with session_factory() as s:
            tags_first = {
                t.model: t.tag
                for t in s.execute(select(ModelTag)).scalars()
            }
        # Override one model so we can confirm overrides survive.
        client.patch(
            "/api/admin/ollama/models/llama3:8b/tag", json={"tag": "both"}
        )
        # New YAML matches 'coder-' — coder-7b auto row should flip to 'code'.
        client.put(
            "/api/admin/ollama/settings",
            json={"tag_heuristics_yaml": "code_patterns:\n  - 'coder-'\n"},
        )
    assert tags_first["llama3:8b"] == "chat"
    assert tags_first["coder-7b"] == "chat"
    with session_factory() as s:
        tags_after = {
            t.model: (t.tag, t.source)
            for t in s.execute(select(ModelTag)).scalars()
        }
    assert tags_after["llama3:8b"] == ("both", "override")
    assert tags_after["coder-7b"] == ("code", "auto")


# =========================================================================
# Per-model metrics
# =========================================================================


def _seed_assistant_messages(
    session_factory: sessionmaker,
    *,
    user_id: int,
    model: str,
    timestamps: list[datetime],
    latencies: list[int] | None = None,
    role: str = "assistant",
    usage_in: int = 100,
    usage_out: int = 50,
) -> None:
    if latencies is None:
        latencies = [200] * len(timestamps)
    with session_factory() as session:
        conv = Conversation(user_id=user_id, mode="chat", model=model)
        session.add(conv)
        session.flush()
        for ts, lat in zip(timestamps, latencies, strict=True):
            session.add(
                Message(
                    conversation_id=conv.id,
                    role=role,
                    content="hi",
                    model=model,
                    usage_in=usage_in,
                    usage_out=usage_out,
                    gen_tps=42.0,
                    latency_ms=lat,
                    ts=ts,
                )
            )
        session.commit()


def test_metrics_summary_aggregates_last_7_days(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_assistant_messages(
        session_factory,
        user_id=1,
        model="llama3:8b",
        timestamps=[now - timedelta(days=2), now - timedelta(days=8)],
    )
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.get("/api/admin/ollama/metrics")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    assert body[0]["model"] == "llama3:8b"
    assert body[0]["calls"] == 1  # 8-day-old row excluded


def test_metrics_summary_excludes_user_and_system_rows(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    base = now - timedelta(hours=1)
    with session_factory() as session:
        conv = Conversation(user_id=1, mode="chat", model="llama3:8b")
        session.add(conv)
        session.flush()
        for role in ("user", "system", "assistant"):
            session.add(
                Message(
                    conversation_id=conv.id,
                    role=role,
                    content="x",
                    model="llama3:8b",
                    ts=base,
                )
            )
        session.commit()

    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.get("/api/admin/ollama/metrics")
    body = r.json()
    assert body[0]["calls"] == 1


def test_metrics_summary_orders_by_calls_desc(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_assistant_messages(
        session_factory,
        user_id=1,
        model="popular",
        timestamps=[now] * 3,
    )
    _seed_assistant_messages(
        session_factory,
        user_id=1,
        model="rare",
        timestamps=[now],
    )
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.get("/api/admin/ollama/metrics")
    body = r.json()
    assert [m["model"] for m in body] == ["popular", "rare"]


def test_metrics_drilldown_returns_last_50_calls(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_assistant_messages(
        session_factory,
        user_id=1,
        model="llama3:8b",
        timestamps=[now - timedelta(seconds=i) for i in range(60)],
    )
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.get("/api/admin/ollama/metrics/llama3:8b")
    body = r.json()
    assert len(body["calls"]) == 50
    # Newest first
    ts_sorted = sorted([c["ts"] for c in body["calls"]], reverse=True)
    assert ts_sorted == [c["ts"] for c in body["calls"]]


def test_metrics_drilldown_p95_latency_python_computed(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    latencies = [10 * i for i in range(1, 21)]  # 10..200
    _seed_assistant_messages(
        session_factory,
        user_id=1,
        model="llama3:8b",
        timestamps=[now] * 20,
        latencies=latencies,
    )
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.get("/api/admin/ollama/metrics/llama3:8b")
    body = r.json()
    p95 = body["p95_latency_ms"]
    assert p95 is not None
    assert 180 <= p95 <= 200


# =========================================================================
# Audit log
# =========================================================================


def test_audit_merges_login_and_admin_rows(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """One login row + one admin audit row → both appear, ts-desc."""
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        # Patch a tag to write one admin_audit row.
        client.patch(
            "/api/admin/ollama/models/llama3:8b/tag", json={"tag": "code"}
        )
        r = client.get("/api/admin/audit")
    assert r.status_code == 200
    body = r.json()
    sources = {e["source"] for e in body["entries"]}
    assert sources == {"login", "admin"}


def test_audit_filters_by_action(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        client.patch(
            "/api/admin/ollama/models/llama3:8b/tag", json={"tag": "code"}
        )
        r = client.get("/api/admin/audit?action=model_tag_set")
    body = r.json()
    assert all(e["action"] == "model_tag_set" for e in body["entries"])
    assert len(body["entries"]) >= 1


def test_audit_filters_by_username(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Filter rows whose actor (admin) or username (login) matches."""
    # Inject an extra login_audit row for a different user so the filter
    # has something to exclude.
    with session_factory() as session:
        session.add(
            LoginAudit(username="bob", success=1, source_ip="127.0.0.1", action="login")
        )
        session.commit()

    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        client.patch(
            "/api/admin/ollama/models/llama3:8b/tag", json={"tag": "code"}
        )
        r = client.get("/api/admin/audit?username=alice")
    body = r.json()
    actors = {e["actor"] for e in body["entries"]}
    assert actors == {"alice"}


def test_audit_pagination(
    settings: Settings, seeded: dict, session_factory: sessionmaker
) -> None:
    """Generate 12 rows; per_page=5&page=2 returns rows 6..10."""
    with session_factory() as session:
        for i in range(12):
            session.add(
                AdminAudit(
                    actor_id=1,
                    action="settings_updated",
                    target_model=None,
                    details_json=json.dumps({"i": i}),
                )
            )
        session.commit()

    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        r = client.get("/api/admin/audit?page=2&per_page=5&action=settings_updated")
    body = r.json()
    assert body["page"] == 2
    assert body["per_page"] == 5
    assert body["total"] == 12
    assert len(body["entries"]) == 5


def test_audit_csv_export(
    settings: Settings, seeded: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded, "alice")
        client.patch(
            "/api/admin/ollama/models/llama3:8b/tag", json={"tag": "code"}
        )
        r = client.get("/api/admin/audit/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "filename=audit.csv" in r.headers.get("content-disposition", "")
    body = r.text
    assert "ts,source,actor,action,target,source_ip,details" in body.splitlines()[0]
    assert any("model_tag_set" in line for line in body.splitlines()[1:])


# =========================================================================
# reapply_heuristics() helper
# =========================================================================


def test_reapply_heuristics_updates_auto_rows(
    session_factory: sessionmaker,
) -> None:
    """An existing auto row whose computed tag changes is updated in place."""
    with session_factory() as session:
        session.add(ModelTag(model="coder-7b", tag="chat", source="auto"))
        session.commit()
    yaml_body = "code_patterns:\n  - 'coder-'\n"
    with session_factory() as session:
        reapply_heuristics(session, ["coder-7b"], yaml_override=yaml_body)
        session.commit()
    with session_factory() as session:
        row = session.execute(
            select(ModelTag).where(ModelTag.model == "coder-7b")
        ).scalar_one()
    assert row.tag == "code"
    assert row.source == "auto"


def test_reapply_heuristics_skips_override_rows(
    session_factory: sessionmaker,
) -> None:
    """An override row is left alone even when the YAML changed."""
    with session_factory() as session:
        session.add(ModelTag(model="coder-7b", tag="both", source="override"))
        session.commit()
    yaml_body = "code_patterns:\n  - 'coder-'\n"
    with session_factory() as session:
        reapply_heuristics(session, ["coder-7b"], yaml_override=yaml_body)
        session.commit()
    with session_factory() as session:
        row = session.execute(
            select(ModelTag).where(ModelTag.model == "coder-7b")
        ).scalar_one()
    assert row.tag == "both"
    assert row.source == "override"


def test_reapply_heuristics_handles_yaml_override_arg(
    session_factory: sessionmaker,
) -> None:
    """yaml_override beats whatever's in the persisted Setting row."""
    with session_factory() as session:
        session.add(
            Setting(
                key=SETTINGS_KEY_TAG_HEURISTICS,
                value="code_patterns:\n  - 'never-matches'\n",
            )
        )
        session.commit()
    with session_factory() as session:
        reapply_heuristics(
            session,
            ["coder-7b"],
            yaml_override="code_patterns:\n  - 'coder-'\n",
        )
        session.commit()
    with session_factory() as session:
        row = session.execute(
            select(ModelTag).where(ModelTag.model == "coder-7b")
        ).scalar_one()
    assert row.tag == "code"
