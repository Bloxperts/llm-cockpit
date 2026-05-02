"""UC-02 — dashboard router + admin Ollama router + services.

Covers the test cases T-10 through T-53 of docs/specs/test/UC-02-dashboard-live.md.

All tests use `FakeLLMChat` + `FakeTelemetry` injected via the create_app
factories — no real Ollama, no real GPU, no subprocess. Each test owns a
fresh in-memory SQLite DB via `tmp_path` so writes don't bleed between
cases.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from cockpit.adapters.fake_chat import FakeLLMChat, model_info
from cockpit.adapters.fake_telemetry import FakeTelemetry, gpu_snapshot
from cockpit.config import Settings
from cockpit.db import make_engine, make_session_factory, upgrade_to_head
from cockpit.main import create_app
from cockpit.models import (
    AdminAudit,
    MetricsSnapshot,
    ModelConfig,
    ModelPerf,
    ModelTag,
    User,
)
from cockpit.ports.llm_chat import (
    ChatChunk,
    LoadedModel,
    OllamaModelNotFound,
    OllamaResponseError,
    OllamaUnreachableError,
)
from cockpit.schemas import DashboardSnapshot
from cockpit.services.audit import write_admin_audit
from cockpit.services.metrics import (
    GpuSampler,
    GpuSamplerState,
    ModelStateSampler,
    ModelStateSamplerState,
    _columns_for,
    _model_state_status,
    assemble_dashboard_snapshot,
)
from cockpit.services.recommendations import score_recommendations
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
def seeded_users(settings: Settings) -> dict[str, dict]:
    """One admin + one chat user."""
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    pw_admin = "AdminPW01!"
    pw_chat = "ChatPW01!"
    try:
        with factory() as session:
            session.add(
                User(
                    username="admin",
                    pw_hash=hash_password(pw_admin, cost=settings.bcrypt_cost),
                    role="admin",
                    must_change_password=0,
                )
            )
            session.add(
                User(
                    username="charlie",
                    pw_hash=hash_password(pw_chat, cost=settings.bcrypt_cost),
                    role="chat",
                    must_change_password=0,
                )
            )
            session.commit()
    finally:
        engine.dispose()
    return {
        "admin": {"role": "admin", "password": pw_admin},
        "charlie": {"role": "chat", "password": pw_chat},
    }


def _build_client(
    settings: Settings,
    *,
    chat: FakeLLMChat | None = None,
    telemetry: FakeTelemetry | None = None,
    skip_samplers: bool = False,
) -> TestClient:
    chat = chat or FakeLLMChat(models=[])
    telemetry = telemetry or FakeTelemetry(snapshots=[])
    app = create_app(
        settings,
        chat_factory=lambda url: chat,
        telemetry_factory=lambda: telemetry,
        skip_db_upgrade=True,
        skip_samplers=skip_samplers,
    )
    # Stash the fakes so tests can poke at them.
    app.state._test_chat = chat
    app.state._test_telemetry = telemetry
    return TestClient(app)


def _login_admin(client: TestClient, seeded_users: dict) -> None:
    client.post(
        "/api/auth/login",
        json={"username": "admin", "password": seeded_users["admin"]["password"]},
    )


def _login_chat_user(client: TestClient, seeded_users: dict) -> None:
    client.post(
        "/api/auth/login",
        json={"username": "charlie", "password": seeded_users["charlie"]["password"]},
    )


# =========================================================================
# T-50..T-53 — services/metrics + services/audit
# =========================================================================


@pytest.mark.asyncio
async def test_gpu_sampler_persists_one_row_per_gpu_per_sample(
    settings: Settings, session_factory: sessionmaker
) -> None:
    """T-50."""
    state = GpuSamplerState()
    telemetry = FakeTelemetry(
        snapshots=[gpu_snapshot(0, vram_used_mb=10000), gpu_snapshot(1, vram_used_mb=20000)]
    )
    sampler = GpuSampler(telemetry=telemetry, session_factory=session_factory, state=state, interval_s=1.0)
    await sampler.sample_once()
    assert state.last_snapshots is not None
    assert len(state.last_snapshots) == 2

    with session_factory() as session:
        rows = list(session.execute(select(MetricsSnapshot)).scalars())
    assert len(rows) == 2
    assert {r.gpu_index for r in rows} == {0, 1}


@pytest.mark.asyncio
async def test_gpu_sampler_writes_nothing_when_no_telemetry(
    session_factory: sessionmaker,
) -> None:
    """`Telemetry.sample()` returns None on hosts without nvidia-smi."""
    state = GpuSamplerState()
    telemetry = FakeTelemetry(return_none=True)
    sampler = GpuSampler(telemetry=telemetry, session_factory=session_factory, state=state)
    await sampler.sample_once()
    assert state.last_snapshots is None
    with session_factory() as session:
        assert session.execute(select(MetricsSnapshot)).first() is None


@pytest.mark.asyncio
async def test_gpu_sampler_records_error_on_telemetry_unavailable(
    session_factory: sessionmaker,
) -> None:
    state = GpuSamplerState()
    telemetry = FakeTelemetry(raise_unavailable=True)
    sampler = GpuSampler(telemetry=telemetry, session_factory=session_factory, state=state)
    await sampler.sample_once()
    assert state.last_error is not None
    assert state.last_success_at is None


@pytest.mark.asyncio
async def test_model_state_sampler_populates_state() -> None:
    """T-51."""
    state = ModelStateSamplerState()
    chat = FakeLLMChat(
        models=[model_info("gemma3:27b"), model_info("qwen3-coder:30b")],
        loaded=[LoadedModel(name="gemma3:27b", size_vram=16 * 1024 * 1024 * 1024, until=None)],
    )
    sampler = ModelStateSampler(chat=chat, state=state, interval_s=1.0)
    await sampler.sample_once()

    assert {m.name for m in state.available_models} == {"gemma3:27b", "qwen3-coder:30b"}
    assert {m.name for m in state.loaded_models} == {"gemma3:27b"}
    assert state.last_success_at is not None
    assert state.last_error is None


@pytest.mark.asyncio
async def test_model_state_sampler_records_error_on_unreachable() -> None:
    state = ModelStateSamplerState()
    chat = FakeLLMChat(raise_on_list_models=OllamaUnreachableError("simulated"))
    sampler = ModelStateSampler(chat=chat, state=state)
    await sampler.sample_once()
    assert state.last_error is not None
    assert state.last_success_at is None


@pytest.mark.parametrize(
    "gpu_count,expected",
    [
        (0, ["on_demand"]),
        (1, ["gpu0", "on_demand"]),
        (2, ["gpu0", "gpu1", "multi_gpu", "on_demand"]),
        (3, ["gpu0", "gpu1", "gpu2", "multi_gpu", "on_demand"]),
        (5, ["gpu0", "gpu1", "gpu2", "gpu3", "gpu4", "multi_gpu", "on_demand"]),
    ],
)
def test_columns_for_various_gpu_counts(gpu_count: int, expected: list[str]) -> None:
    """T-12."""
    assert _columns_for(gpu_count) == expected


def test_model_state_status_healthy() -> None:
    """T-14 happy."""
    g = GpuSamplerState(last_snapshots=[], last_success_at=100.0)
    m = ModelStateSamplerState(last_success_at=100.0)
    assert _model_state_status(g, m, now=110.0) == "healthy"


def test_model_state_status_degraded() -> None:
    """One sampler erroring → degraded."""
    g = GpuSamplerState(last_snapshots=None, last_error="oops", last_error_at=100.0)
    m = ModelStateSamplerState(last_success_at=100.0)
    assert _model_state_status(g, m, now=110.0) == "degraded"


def test_model_state_status_ollama_unreachable() -> None:
    """Model sampler failing > 30 s → ollama_unreachable."""
    g = GpuSamplerState(last_snapshots=[], last_success_at=100.0)
    m = ModelStateSamplerState(last_success_at=None, last_error="boom", last_error_at=100.0)
    assert _model_state_status(g, m, now=131.0) == "ollama_unreachable"


def test_assemble_dashboard_snapshot_validates_against_schema(
    settings: Settings, session_factory: sessionmaker
) -> None:
    """T-10 — snapshot validates against the DashboardSnapshot pydantic schema."""
    g = GpuSamplerState(last_snapshots=[gpu_snapshot(0), gpu_snapshot(1)], last_success_at=1.0)
    m = ModelStateSamplerState(
        available_models=[model_info("gemma3:27b")],
        loaded_models=[LoadedModel(name="gemma3:27b", size_vram=16 * 1024 * 1024 * 1024, until=None)],
        last_success_at=1.0,
    )
    with session_factory() as session:
        # Pre-seed a model_tag so the card carries 'chat'.
        session.add(ModelTag(model="gemma3:27b", tag="chat", source="auto"))
        session.commit()

        payload = assemble_dashboard_snapshot(session=session, gpu_state=g, model_state=m, now=2.0)

    snap = DashboardSnapshot.model_validate(payload)
    assert {gp.index for gp in snap.gpus} == {0, 1}
    assert "multi_gpu" in snap.columns
    assert snap.last_calls == []  # T-13
    card = snap.models[0]
    assert card.name == "gemma3:27b"
    assert card.tag == "chat"
    assert card.actual.loaded is True
    assert card.metadata.release_date_label is not None
    assert card.context.estimate_confidence in {"unknown", "estimated", "measured"}


def test_write_admin_audit_inserts_row(session_factory: sessionmaker) -> None:
    """T-53."""
    with session_factory() as session:
        row = write_admin_audit(
            session,
            actor_id=42,
            action="model_place",
            target_model="gemma3:27b",
            details={"old": "available", "new": "gpu0"},
            source_ip="127.0.0.1",
        )
        session.commit()
        assert row.id is not None

        fetched = session.query(AdminAudit).filter_by(id=row.id).one()
        assert fetched.actor_id == 42
        assert fetched.action == "model_place"
        assert fetched.target_model == "gemma3:27b"
        assert json.loads(fetched.details_json) == {"old": "available", "new": "gpu0"}
        assert fetched.source_ip == "127.0.0.1"


# =========================================================================
# T-10..T-17 — dashboard router
# =========================================================================


def test_dashboard_snapshot_requires_settled_session(settings: Settings, seeded_users: dict) -> None:
    """T-15."""
    client = _build_client(settings)
    # No login → 401.
    r = client.get("/api/dashboard/snapshot")
    assert r.status_code == 401


def test_dashboard_snapshot_returns_payload_validating_schema(settings: Settings, seeded_users: dict) -> None:
    """T-10 + T-12 + T-13."""
    chat = FakeLLMChat(
        models=[model_info("gemma3:27b"), model_info("qwen3-coder:30b")],
        loaded=[],
    )
    tel = FakeTelemetry(
        snapshots=[
            gpu_snapshot(0, vram_used_mb=8000),
            gpu_snapshot(1, vram_used_mb=12000),
        ]
    )
    client = _build_client(settings, chat=chat, telemetry=tel)
    with client:
        _login_admin(client, seeded_users)
        r = client.get("/api/dashboard/snapshot")
    assert r.status_code == 200
    snap = DashboardSnapshot.model_validate(r.json())
    assert len(snap.gpus) == 2
    assert "multi_gpu" in snap.columns
    assert {m.name for m in snap.models} == {"gemma3:27b", "qwen3-coder:30b"}
    assert snap.last_calls == []
    assert snap.status in ("healthy", "degraded", "ollama_unreachable")


def test_dashboard_no_gpu_collapses_columns(settings: Settings, seeded_users: dict) -> None:
    """T-11."""
    client = _build_client(
        settings,
        chat=FakeLLMChat(models=[model_info("gemma3:27b")]),
        telemetry=FakeTelemetry(return_none=True),
    )
    with client:
        _login_admin(client, seeded_users)
        r = client.get("/api/dashboard/snapshot")
    assert r.status_code == 200
    snap = DashboardSnapshot.model_validate(r.json())
    assert snap.gpus == []
    assert snap.columns == ["on_demand"]


def test_dashboard_stream_endpoint_exists_and_requires_auth(
    settings: Settings,
) -> None:
    """T-17 — auth gate. The full SSE iteration is integration-test territory
    (TestClient's sync executor doesn't handle streaming generators reliably
    across asyncio.sleep boundaries). The event-generator's payload shape is
    exercised by `test_stream_event_generator_yields_snapshot` below.
    """
    client = _build_client(settings, skip_samplers=True)
    with client:
        # No login → 401 before the stream even starts.
        r = client.get("/api/dashboard/stream")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_stream_event_generator_yields_snapshot(
    settings: Settings,
) -> None:
    """Drive the SSE event generator directly: assert it yields one event
    per loop iteration and stops cleanly when the request reports
    disconnection on the next poll. Avoids TestClient's streaming quirks.
    Covers the sleep-then-disconnect path of the generator.
    """
    from cockpit.routers.dashboard import stream_event_generator

    client = _build_client(settings, skip_samplers=True)
    with client:

        class _DisconnectAfterOne:
            def __init__(self, app) -> None:
                self.app = app
                self._calls = 0

            async def is_disconnected(self) -> bool:
                self._calls += 1
                return self._calls > 1  # connected on first poll, then disconnected

        req = _DisconnectAfterOne(client.app)
        events = [event async for event in stream_event_generator(req, interval_s=0.001)]
        assert len(events) == 1
        assert events[0]["event"] == "snapshot"
        payload = json.loads(events[0]["data"])
        assert "gpus" in payload
        assert "columns" in payload


@pytest.mark.asyncio
async def test_stream_event_generator_handles_cancellation(
    settings: Settings,
) -> None:
    """If the generator is cancelled mid-sleep, the CancelledError handler
    cleans up and exits rather than propagating up the SSE response."""
    from cockpit.routers.dashboard import stream_event_generator

    client = _build_client(settings, skip_samplers=True)
    with client:

        class _AlwaysConnected:
            def __init__(self, app) -> None:
                self.app = app

            async def is_disconnected(self) -> bool:
                return False

        req = _AlwaysConnected(client.app)
        gen = stream_event_generator(req, interval_s=10.0)
        # First event arrives immediately; then the generator is parked in
        # asyncio.sleep(10). aclose() raises CancelledError into it; the
        # generator's except clause catches and returns.
        first = await gen.__anext__()
        assert first["event"] == "snapshot"
        await gen.aclose()


@pytest.mark.asyncio
async def test_stream_event_generator_exits_on_disconnect(
    settings: Settings,
) -> None:
    """Disconnect before the first event → generator yields nothing and exits."""
    from cockpit.routers.dashboard import stream_event_generator

    client = _build_client(settings, skip_samplers=True)
    with client:

        class _AlwaysDisconnected:
            def __init__(self, app) -> None:
                self.app = app

            async def is_disconnected(self) -> bool:
                return True

        req = _AlwaysDisconnected(client.app)
        events = [e async for e in stream_event_generator(req, interval_s=0.001)]
        assert events == []


# =========================================================================
# T-20..T-28 — placement transition
# =========================================================================


def _seed_models_state(client: TestClient, *, names: list[str]) -> None:
    """Push a list of available models into app.state.model_state.available_models
    so the route's GPU-count + placement logic operates on a known set.
    """
    client.app.state.model_state.available_models = [model_info(n) for n in names]


def _seed_gpu_state(client: TestClient, *, gpu_count: int) -> None:
    client.app.state.gpu_state.last_snapshots = [gpu_snapshot(i) for i in range(gpu_count)]


def test_place_gpu0_sends_keep_alive_24h_and_main_gpu_0(settings: Settings, seeded_users: dict) -> None:
    """T-20 + T-21."""
    chat = FakeLLMChat(
        models=[model_info("gemma3:27b")],
        loaded=[LoadedModel(name="gemma3:27b", size_vram=16 * 1024 * 1024 * 1024, until=None)],
        tokens=["ok"],
    )
    tel = FakeTelemetry(
        snapshots=[
            gpu_snapshot(0, vram_used_mb=2000),
            gpu_snapshot(1, vram_used_mb=2000),
        ]
    )
    client = _build_client(settings, chat=chat, telemetry=tel, skip_samplers=True)
    with client:
        _seed_gpu_state(client, gpu_count=2)
        _login_admin(client, seeded_users)

        r = client.post(
            "/api/admin/ollama/models/gemma3:27b/place",
            json={"placement": "gpu0"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"]["main_gpu"] == 0
    assert body["applied"]["keep_alive_seconds"] == 24 * 3600
    assert body["loaded_now"] is True

    # The placement handler issues one warm-up `chat_stream` call (with the
    # right options) and then polls `loaded()` until the model shows up.
    # Use `calls_of` rather than `last_call`: the loaded poll overwrites
    # the latter.
    chat_calls = chat.calls_of("chat_stream")
    assert len(chat_calls) == 1
    warm_up = chat_calls[0]
    assert warm_up["model"] == "gemma3:27b"
    assert warm_up["options"]["main_gpu"] == 0
    assert warm_up["options"]["keep_alive"] == 24 * 3600

    # T-21: model_config row + admin_audit row.
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            cfg = session.query(ModelConfig).filter_by(model="gemma3:27b").one()
            assert cfg.placement == "gpu0"
            audits = list(
                session.execute(select(AdminAudit).where(AdminAudit.action == "model_place")).scalars()
            )
            assert len(audits) == 1
            details = json.loads(audits[0].details_json)
            assert details["new"] == "gpu0"
            assert details["applied"]["main_gpu"] == 0
    finally:
        engine.dispose()


def test_place_multi_gpu_sends_num_gpu_99(settings: Settings, seeded_users: dict) -> None:
    """T-22."""
    chat = FakeLLMChat(
        models=[model_info("m")],
        loaded=[LoadedModel(name="m", size_vram=1, until=None)],
        tokens=["ok"],
    )
    tel = FakeTelemetry(snapshots=[gpu_snapshot(0), gpu_snapshot(1)])
    client = _build_client(settings, chat=chat, telemetry=tel, skip_samplers=True)
    with client:
        _seed_gpu_state(client, gpu_count=2)
        _login_admin(client, seeded_users)
        r = client.post(
            "/api/admin/ollama/models/m/place",
            json={"placement": "multi_gpu"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["applied"]["num_gpu"] == 99
    assert body["applied"].get("main_gpu") is None
    warm = chat.calls_of("chat_stream")[0]
    assert warm["options"]["num_gpu"] == 99
    assert "main_gpu" not in warm["options"]


def test_place_on_demand_sends_keep_alive_zero(settings: Settings, seeded_users: dict) -> None:
    """T-23 + T-24."""
    chat = FakeLLMChat(
        models=[model_info("m")],
        loaded=[],  # not loaded; matches keep_alive=0
        tokens=["ok"],
    )
    tel = FakeTelemetry(snapshots=[gpu_snapshot(0)])
    client = _build_client(settings, chat=chat, telemetry=tel, skip_samplers=True)
    with client:
        _seed_gpu_state(client, gpu_count=1)
        _login_admin(client, seeded_users)
        r = client.post(
            "/api/admin/ollama/models/m/place",
            json={"placement": "on_demand"},
        )
    assert r.status_code == 200
    assert r.json()["applied"]["keep_alive_seconds"] == 0
    warm = chat.calls_of("chat_stream")[0]
    assert warm["options"]["keep_alive"] == 0


def test_place_invalid_placement_returns_422(settings: Settings, seeded_users: dict) -> None:
    """T-25."""
    chat = FakeLLMChat(models=[model_info("m")])
    client = _build_client(settings, chat=chat, telemetry=FakeTelemetry(return_none=True), skip_samplers=True)
    with client:
        _seed_gpu_state(client, gpu_count=0)
        _login_admin(client, seeded_users)
        r = client.post(
            "/api/admin/ollama/models/m/place",
            json={"placement": "gpu5"},
        )
    assert r.status_code == 422
    assert r.json()["detail"]["detail"] == "invalid_placement"


def test_place_gpu4_allowed_on_five_gpu_host(settings: Settings, seeded_users: dict) -> None:
    chat = FakeLLMChat(
        models=[model_info("m")],
        loaded=[LoadedModel(name="m", size_vram=1, until=None)],
        tokens=["ok"],
    )
    client = _build_client(
        settings,
        chat=chat,
        telemetry=FakeTelemetry(snapshots=[gpu_snapshot(i) for i in range(5)]),
        skip_samplers=True,
    )
    with client:
        _seed_gpu_state(client, gpu_count=5)
        _login_admin(client, seeded_users)
        r = client.post("/api/admin/ollama/models/m/place", json={"placement": "gpu4"})
    assert r.status_code == 200, r.text
    assert r.json()["applied"]["main_gpu"] == 4
    assert chat.calls_of("chat_stream")[0]["options"]["main_gpu"] == 4


def test_place_gpu4_rejected_on_two_gpu_host(settings: Settings, seeded_users: dict) -> None:
    chat = FakeLLMChat(models=[model_info("m")])
    client = _build_client(settings, chat=chat, telemetry=FakeTelemetry(snapshots=[]), skip_samplers=True)
    with client:
        _seed_gpu_state(client, gpu_count=2)
        _login_admin(client, seeded_users)
        r = client.post("/api/admin/ollama/models/m/place", json={"placement": "gpu4"})
    assert r.status_code == 422


def test_place_permanent_keep_alive_maps_negative(settings: Settings, seeded_users: dict) -> None:
    chat = FakeLLMChat(
        models=[model_info("m")],
        loaded=[LoadedModel(name="m", size_vram=1, until=None)],
        tokens=["ok"],
    )
    client = _build_client(settings, chat=chat, telemetry=FakeTelemetry(snapshots=[gpu_snapshot(0)]), skip_samplers=True)
    with client:
        _seed_gpu_state(client, gpu_count=1)
        _login_admin(client, seeded_users)
        r = client.post(
            "/api/admin/ollama/models/m/place",
            json={"placement": "gpu0", "keep_alive_mode": "permanent"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["applied"]["keep_alive"] == -1
    assert chat.calls_of("chat_stream")[0]["options"]["keep_alive"] == -1


def test_place_detects_main_gpu_mismatch(settings: Settings, seeded_users: dict) -> None:
    """T-26 — mismatch=True when post-warm VRAM grew on a different GPU than requested."""
    chat = FakeLLMChat(
        models=[model_info("m")],
        loaded=[LoadedModel(name="m", size_vram=1, until=None)],
        tokens=["ok"],
    )

    # Pre + post snapshots: GPU0 stays at 2000, GPU1 grew to 18000.
    snapshots_before = [
        gpu_snapshot(0, vram_used_mb=2000),
        gpu_snapshot(1, vram_used_mb=2000),
    ]
    snapshots_after = [
        gpu_snapshot(0, vram_used_mb=2000),
        gpu_snapshot(1, vram_used_mb=18000),
    ]
    call_count = {"n": 0}

    class MismatchTelemetry(FakeTelemetry):
        async def sample(self):
            call_count["n"] += 1
            return snapshots_before if call_count["n"] == 1 else snapshots_after

    tel = MismatchTelemetry(snapshots=[])
    client = _build_client(settings, chat=chat, telemetry=tel, skip_samplers=True)
    with client:
        _seed_gpu_state(client, gpu_count=2)
        _login_admin(client, seeded_users)
        r = client.post(
            "/api/admin/ollama/models/m/place",
            json={"placement": "gpu0"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["mismatch"] is True
    assert body["main_gpu_actual"] == 1


def test_place_requires_admin(settings: Settings, seeded_users: dict) -> None:
    """T-27."""
    chat = FakeLLMChat(models=[model_info("m")])
    client = _build_client(
        settings, chat=chat, telemetry=FakeTelemetry(snapshots=[gpu_snapshot(0)]), skip_samplers=True
    )
    with client:
        _seed_gpu_state(client, gpu_count=1)
        _login_chat_user(client, seeded_users)
        r = client.post(
            "/api/admin/ollama/models/m/place",
            json={"placement": "gpu0"},
        )
    assert r.status_code == 403


# =========================================================================
# T-40..T-42 — pull / delete / settings
# =========================================================================


def test_delete_model_removes_config_and_audits(settings: Settings, seeded_users: dict) -> None:
    """T-41."""
    chat = FakeLLMChat(models=[model_info("m")], known_models={"m"})
    client = _build_client(settings, chat=chat, telemetry=FakeTelemetry(snapshots=[]), skip_samplers=True)

    # Pre-seed a model_config row.
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            session.add(ModelConfig(model="m", placement="available"))
            session.commit()
    finally:
        engine.dispose()

    with client:
        _login_admin(client, seeded_users)
        r = client.delete("/api/admin/ollama/models/m")
    assert r.status_code == 204
    assert chat.deleted == ["m"]

    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            assert session.query(ModelConfig).filter_by(model="m").first() is None
            audits = list(
                session.execute(select(AdminAudit).where(AdminAudit.action == "model_delete")).scalars()
            )
            assert len(audits) == 1
    finally:
        engine.dispose()


def test_delete_model_404_when_not_found(settings: Settings, seeded_users: dict) -> None:
    chat = FakeLLMChat(models=[], known_models={"only-known"})
    client = _build_client(settings, chat=chat, telemetry=FakeTelemetry(snapshots=[]), skip_samplers=True)
    with client:
        _login_admin(client, seeded_users)
        r = client.delete("/api/admin/ollama/models/ghost")
    assert r.status_code == 404


def test_settings_patch_writes_only_present_fields(settings: Settings, seeded_users: dict) -> None:
    """T-42."""
    chat = FakeLLMChat(models=[model_info("m")])
    client = _build_client(settings, chat=chat, telemetry=FakeTelemetry(snapshots=[]), skip_samplers=True)

    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            session.add(ModelConfig(model="m", placement="available", num_ctx_default=4096))
            session.commit()
    finally:
        engine.dispose()

    with client:
        _login_admin(client, seeded_users)
        r = client.patch(
            "/api/admin/ollama/models/m/settings",
            json={"single_flight": True, "notes": "loud at idle"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == {"single_flight": True, "notes": "loud at idle"}

    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            cfg = session.query(ModelConfig).filter_by(model="m").one()
            assert cfg.single_flight == 1
            assert cfg.notes == "loud at idle"
            assert cfg.num_ctx_default == 4096  # untouched
            audits = list(
                session.execute(
                    select(AdminAudit).where(AdminAudit.action == "model_settings_patch")
                ).scalars()
            )
            assert len(audits) == 1
    finally:
        engine.dispose()


def test_pull_model_streams_and_creates_default_config(settings: Settings, seeded_users: dict) -> None:
    """T-40."""
    from cockpit.ports.llm_chat import PullProgress

    chat = FakeLLMChat(
        pull_progress=[
            PullProgress(status="pulling manifest"),
            PullProgress(status="pulling abcd", digest="sha256:abcd", total=100, completed=50),
            PullProgress(status="success"),
        ]
    )
    client = _build_client(settings, chat=chat, telemetry=FakeTelemetry(snapshots=[]), skip_samplers=True)
    with client:
        _login_admin(client, seeded_users)
        with client.stream("POST", "/api/admin/ollama/models/new-model/pull") as r:
            assert r.status_code == 200
            events = []
            for line in r.iter_lines():
                if line and line.startswith("data:"):
                    events.append(line[len("data:") :].strip())
    assert any("pulling manifest" in e for e in events)
    assert any("success" in e for e in events)

    # Default model_config row created.
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            cfg = session.query(ModelConfig).filter_by(model="new-model").one()
            assert cfg.placement == "on_demand"
            audits = list(
                session.execute(select(AdminAudit).where(AdminAudit.action == "model_pull")).scalars()
            )
            assert len(audits) == 1
    finally:
        engine.dispose()


# =========================================================================
# T-30..T-34 — perf harness
# =========================================================================


def _make_perf_chat() -> FakeLLMChat:
    """Fake that supports both warm-up (drops on keep_alive=0) and the
    full perf harness sequence."""
    final = ChatChunk(
        delta="ok",
        done=True,
        usage_in=200,
        usage_out=200,
        eval_duration_ns=1_000_000_000,  # 1s → 200 tps
        prompt_eval_duration_ns=500_000_000,
        total_duration_ns=1_500_000_000,
    )
    fake = FakeLLMChat(
        models=[model_info("m")],
        # Loaded becomes empty after the drop — but FakeLLMChat returns the
        # canned list always. We cope by having the perf harness's wait
        # functions tolerate timeouts (they return False / True respectively).
        loaded=[],
        tokens=["o", "k"],
        final_chunk=final,
    )
    return fake


def _iter_sse_events(response) -> Any:
    event = "message"
    for line in response.iter_lines():
        if not line:
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            yield event, json.loads(line[len("data:") :].strip())
            event = "message"


def test_perf_test_emits_stage_sequence_and_writes_perf_row(settings: Settings, seeded_users: dict) -> None:
    """T-30 + T-31."""
    chat = _make_perf_chat()
    client = _build_client(
        settings,
        chat=chat,
        telemetry=FakeTelemetry(
            snapshots=[
                gpu_snapshot(0, vram_used_mb=2000),
                gpu_snapshot(1, vram_used_mb=2000),
            ]
        ),
        skip_samplers=True,
    )
    with client:
        _seed_gpu_state(client, gpu_count=2)
        _login_admin(client, seeded_users)

        # Pre-seed a placement so the restore stage exercises a real branch.
        with make_session_factory(make_engine(settings.db_url))() as s:
            s.add(ModelConfig(model="m", placement="gpu0"))
            s.commit()

        with client.stream(
            "POST",
            "/api/admin/ollama/models/m/perf-test",
            json={"contexts": [4096, 16384]},
        ) as r:
            assert r.status_code == 200
            stage_names: list[str] = []
            saw_progress = False
            saw_result = False
            for event, payload in _iter_sse_events(r):
                if event == "stage":
                    stage_names.append(payload["name"])
                if event == "progress":
                    saw_progress = True
                    assert "stage" in payload
                    assert "elapsed_ms" in payload
                if event == "result":
                    saw_result = True
                    assert payload["gpu_layout_diff"] == {
                        "gpu0_vram_growth_mb": 0,
                        "gpu1_vram_growth_mb": 0,
                    }

    assert "lock" in stage_names
    assert "unload" in stage_names
    assert "cold_load" in stage_names
    assert "throughput" in stage_names
    assert "context_probe" in stage_names
    assert "persist" in stage_names
    assert "restore" in stage_names
    assert saw_progress
    assert saw_result

    # T-31: model_perf row written.
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            rows = list(session.execute(select(ModelPerf)).scalars())
            assert len(rows) >= 1
            row = rows[-1]
            assert row.model == "m"
            assert row.cold_load_seconds is not None
            assert row.warm_load_seconds is not None
            assert row.throughput_tps is not None
            assert row.benchmark_profile in {"gpu0", "gpu1", "multi_gpu", "on_demand"}
            audits = list(
                session.execute(select(AdminAudit).where(AdminAudit.action == "model_perf_test")).scalars()
            )
            assert len(audits) == 4
    finally:
        engine.dispose()


def test_perf_test_persists_profile_error_note(settings: Settings, seeded_users: dict) -> None:
    class FailingProfileChat(FakeLLMChat):
        async def chat_stream(self, **kwargs):
            self._record("chat_stream", **kwargs)
            raise OllamaResponseError(500, "profile exploded")
            yield  # pragma: no cover

    chat = FailingProfileChat(models=[model_info("m")], known_models={"m"})
    client = _build_client(
        settings,
        chat=chat,
        telemetry=FakeTelemetry(snapshots=[gpu_snapshot(0)]),
        skip_samplers=True,
    )
    with client:
        _seed_gpu_state(client, gpu_count=1)
        _login_admin(client, seeded_users)
        with client.stream(
            "POST",
            "/api/admin/ollama/models/m/perf-test",
            json={"contexts": [4096], "profiles": ["gpu0"]},
        ) as r:
            assert r.status_code == 200
            events = list(_iter_sse_events(r))
            assert any(event == "error" for event, _payload in events)

    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            row = session.execute(select(ModelPerf).where(ModelPerf.model == "m")).scalar_one()
            assert row.benchmark_profile == "gpu0"
            assert row.notes is not None
            assert "profile failed" in row.notes
    finally:
        engine.dispose()


def test_perf_test_retests_only_requested_profile(settings: Settings, seeded_users: dict) -> None:
    chat = FakeLLMChat(models=[model_info("m")], known_models={"m"})
    client = _build_client(
        settings,
        chat=chat,
        telemetry=FakeTelemetry(snapshots=[gpu_snapshot(0), gpu_snapshot(1)]),
        skip_samplers=True,
    )
    with client:
        _seed_gpu_state(client, gpu_count=2)
        _login_admin(client, seeded_users)
        with client.stream(
            "POST",
            "/api/admin/ollama/models/m/perf-test",
            json={"contexts": [4096], "profiles": ["gpu1"]},
        ) as r:
            assert r.status_code == 200
            events = list(_iter_sse_events(r))

    profile_events = [payload["profile"] for event, payload in events if event == "profile"]
    assert profile_events == ["gpu1"]
    result = next(payload for event, payload in events if event == "result")
    assert [row["benchmark_profile"] for row in result["profiles"]] == ["gpu1"]

    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            rows = list(session.execute(select(ModelPerf).where(ModelPerf.model == "m")).scalars())
            assert len(rows) == 1
            assert rows[0].benchmark_profile == "gpu1"
    finally:
        engine.dispose()


# =========================================================================
# Role gates across the admin router
# =========================================================================


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/api/admin/ollama/models/m/place", {"placement": "gpu0"}),
        ("DELETE", "/api/admin/ollama/models/m", None),
        ("PATCH", "/api/admin/ollama/models/m/settings", {"notes": "x"}),
        ("POST", "/api/admin/ollama/models/m/pull", None),
        ("POST", "/api/admin/ollama/models/m/perf-test", {"contexts": [4096]}),
    ],
)
def test_admin_endpoints_block_non_admins(
    settings: Settings, seeded_users: dict, method: str, path: str, body
) -> None:
    """T-27 generalised across every admin endpoint."""
    client = _build_client(
        settings,
        chat=FakeLLMChat(models=[model_info("m")]),
        telemetry=FakeTelemetry(snapshots=[gpu_snapshot(0)]),
        skip_samplers=True,
    )
    with client:
        _seed_gpu_state(client, gpu_count=1)
        _login_chat_user(client, seeded_users)
        r = client.request(method, path, json=body) if body is not None else client.request(method, path)
    assert r.status_code == 403, (method, path)


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/dashboard/snapshot", None),
        ("POST", "/api/admin/ollama/models/m/place", {"placement": "gpu0"}),
    ],
)
def test_dashboard_and_admin_block_unauth(settings: Settings, method: str, path: str, body) -> None:
    client = _build_client(settings, skip_samplers=True)
    with client:
        if body is None:
            r = client.request(method, path)
        else:
            r = client.request(method, path, json=body)
    assert r.status_code == 401


# =========================================================================
# Coverage bumps: error branches across admin_ollama.py
# =========================================================================


def test_place_404_when_model_not_found(settings: Settings, seeded_users: dict) -> None:
    chat = FakeLLMChat(models=[], known_models={"only-known"})
    client = _build_client(
        settings,
        chat=chat,
        telemetry=FakeTelemetry(snapshots=[gpu_snapshot(0)]),
        skip_samplers=True,
    )
    with client:
        _seed_gpu_state(client, gpu_count=1)
        _login_admin(client, seeded_users)
        r = client.post(
            "/api/admin/ollama/models/ghost/place",
            json={"placement": "gpu0"},
        )
    assert r.status_code == 404


def test_place_503_when_ollama_unreachable(settings: Settings, seeded_users: dict) -> None:
    """Warm-up that raises OllamaUnreachableError → 503 ollama_unreachable."""

    class UnreachableChat(FakeLLMChat):
        async def chat_stream(self, **_kwargs):
            raise OllamaUnreachableError("simulated")
            yield  # unreachable but makes this an async generator

    chat = UnreachableChat(models=[model_info("m")])
    client = _build_client(
        settings,
        chat=chat,
        telemetry=FakeTelemetry(snapshots=[gpu_snapshot(0)]),
        skip_samplers=True,
    )
    with client:
        _seed_gpu_state(client, gpu_count=1)
        _login_admin(client, seeded_users)
        r = client.post(
            "/api/admin/ollama/models/m/place",
            json={"placement": "gpu0"},
        )
    assert r.status_code == 503
    assert r.json()["detail"]["detail"] == "ollama_unreachable"


def test_pull_emits_error_event_on_unreachable(settings: Settings, seeded_users: dict) -> None:
    class UnreachablePullChat(FakeLLMChat):
        async def pull_model(self, model: str):
            raise OllamaUnreachableError("simulated")
            yield  # unreachable

    chat = UnreachablePullChat()
    client = _build_client(settings, chat=chat, telemetry=FakeTelemetry(snapshots=[]), skip_samplers=True)
    with client:
        _login_admin(client, seeded_users)
        with client.stream("POST", "/api/admin/ollama/models/x/pull") as r:
            assert r.status_code == 200
            saw_error = False
            for line in r.iter_lines():
                if line and line.startswith("data:") and "ollama_unreachable" in line:
                    saw_error = True
                    break
            assert saw_error


def test_settings_patch_writes_all_four_fields(settings: Settings, seeded_users: dict) -> None:
    chat = FakeLLMChat(models=[model_info("m")])
    client = _build_client(settings, chat=chat, telemetry=FakeTelemetry(snapshots=[]), skip_samplers=True)

    with client:
        _login_admin(client, seeded_users)
        r = client.patch(
            "/api/admin/ollama/models/m/settings",
            json={
                "keep_alive_seconds": 3600,
                "num_ctx_default": 8192,
                "single_flight": False,
                "notes": "rotates fans",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == {
        "keep_alive_seconds": 3600,
        "num_ctx_default": 8192,
        "single_flight": False,
        "notes": "rotates fans",
    }


def test_delete_503_when_ollama_unreachable(settings: Settings, seeded_users: dict) -> None:
    class UnreachableDeleteChat(FakeLLMChat):
        async def delete_model(self, model: str) -> None:
            raise OllamaUnreachableError("simulated")

    chat = UnreachableDeleteChat(models=[model_info("m")], known_models={"m"})
    client = _build_client(settings, chat=chat, telemetry=FakeTelemetry(snapshots=[]), skip_samplers=True)
    with client:
        _login_admin(client, seeded_users)
        r = client.delete("/api/admin/ollama/models/m")
    assert r.status_code == 503


def test_perf_test_emits_error_on_model_not_found(settings: Settings, seeded_users: dict) -> None:
    class NotFoundColdChat(FakeLLMChat):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._chat_calls = 0

        async def chat_stream(self, **kwargs):
            self._chat_calls += 1
            # First call is the unload drop. Second is cold_load → not found.
            if self._chat_calls == 1:
                # Drop succeeds (no chunks needed).
                return
                yield  # unreachable
            raise OllamaModelNotFound(kwargs.get("model", "?"))
            yield  # unreachable

    chat = NotFoundColdChat(models=[model_info("ghost")], known_models={"ghost"})
    client = _build_client(
        settings,
        chat=chat,
        telemetry=FakeTelemetry(snapshots=[gpu_snapshot(0)]),
        skip_samplers=True,
    )
    with client:
        _seed_gpu_state(client, gpu_count=1)
        _login_admin(client, seeded_users)

        with client.stream(
            "POST",
            "/api/admin/ollama/models/ghost/perf-test",
            json={"contexts": [4096]},
        ) as r:
            assert r.status_code == 200
            saw_error = False
            for line in r.iter_lines():
                if line and line.startswith("data:") and "model_not_found" in line:
                    saw_error = True
                    break
            assert saw_error


def test_perf_test_cancelled_event_skips_perf_row(
    settings: Settings, seeded_users: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cockpit.routers import admin_ollama as ao

    monkeypatch.setattr(ao, "PERF_HEARTBEAT_INTERVAL_S", 0.01)
    monkeypatch.setattr(ao, "LOADED_POLL_INTERVAL_S", 0.01)
    app_holder: dict[str, Any] = {}

    class CancelOnSampleTelemetry(FakeTelemetry):
        async def sample(self):
            run = app_holder["app"].state.perf_test_runs.get("m")
            if run is not None:
                run.cancel_event.set()
            return await super().sample()

    chat = FakeLLMChat(
        models=[model_info("m")],
        loaded=[],
        tokens=["ok"],
    )
    client = _build_client(
        settings,
        chat=chat,
        telemetry=CancelOnSampleTelemetry(snapshots=[gpu_snapshot(0)]),
        skip_samplers=True,
    )
    app_holder["app"] = client.app
    with client:
        _seed_gpu_state(client, gpu_count=1)
        _login_admin(client, seeded_users)
        with client.stream(
            "POST",
            "/api/admin/ollama/models/m/perf-test",
            json={"contexts": [4096]},
        ) as r:
            assert r.status_code == 200
            saw_cancelled = False
            for event, payload in _iter_sse_events(r):
                if event == "cancelled":
                    saw_cancelled = True
                    assert payload["stage_at_cancel"] in {"cold_load", "restore"}
                    assert "elapsed_ms" in payload
                    break
            assert saw_cancelled

    with make_session_factory(make_engine(settings.db_url))() as session:
        assert list(session.execute(select(ModelPerf)).scalars()) == []
        audits = list(
            session.execute(select(AdminAudit).where(AdminAudit.action == "model_perf_test_cancel")).scalars()
        )
        assert len(audits) == 1


def test_perf_test_cancel_route_flips_active_run_event(settings: Settings, seeded_users: dict) -> None:
    from cockpit.routers import admin_ollama as ao

    client = _build_client(
        settings,
        chat=FakeLLMChat(models=[model_info("m")]),
        telemetry=FakeTelemetry(snapshots=[gpu_snapshot(0)]),
        skip_samplers=True,
    )
    with client:
        _login_admin(client, seeded_users)
        run = ao._PerfRunState(model="m")
        client.app.state.perf_test_runs["m"] = run
        r = client.post("/api/admin/ollama/models/m/perf-test/cancel")
    assert r.status_code == 200
    assert r.json() == {"cancelled": True}
    assert run.cancel_event.is_set()


def test_perf_test_cancel_route_returns_false_without_active_run(
    settings: Settings, seeded_users: dict
) -> None:
    client = _build_client(
        settings,
        chat=FakeLLMChat(models=[model_info("m")]),
        telemetry=FakeTelemetry(snapshots=[gpu_snapshot(0)]),
        skip_samplers=True,
    )
    with client:
        _login_admin(client, seeded_users)
        r = client.post("/api/admin/ollama/models/m/perf-test/cancel")
    assert r.status_code == 200
    assert r.json() == {"cancelled": False}


@pytest.mark.asyncio
async def test_perf_await_helper_emits_heartbeat_on_slow_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cockpit.routers import admin_ollama as ao

    monkeypatch.setattr(ao, "PERF_HEARTBEAT_INTERVAL_S", 0.01)
    state = ao._PerfRunState(model="m")
    state.stage = "throughput"
    seen: list[tuple[str, dict[str, Any]]] = []
    result: dict[str, Any] = {}
    async for event in ao._await_with_heartbeat(asyncio.sleep(0.03, result=42), state, result):
        seen.append((event["event"], json.loads(event["data"])))
    assert result["value"] == 42
    assert any(event == "heartbeat" and payload["stage"] == "throughput" for event, payload in seen)


def test_perf_test_emits_error_on_unreachable(settings: Settings, seeded_users: dict) -> None:
    class UnreachableColdChat(FakeLLMChat):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._chat_calls = 0

        async def chat_stream(self, **kwargs):
            self._chat_calls += 1
            if self._chat_calls == 1:
                return
                yield  # unreachable
            raise OllamaUnreachableError("ollama down")
            yield  # unreachable

    chat = UnreachableColdChat(models=[model_info("m")], known_models={"m"})
    client = _build_client(
        settings,
        chat=chat,
        telemetry=FakeTelemetry(snapshots=[gpu_snapshot(0)]),
        skip_samplers=True,
    )
    with client:
        _seed_gpu_state(client, gpu_count=1)
        _login_admin(client, seeded_users)

        with client.stream(
            "POST",
            "/api/admin/ollama/models/m/perf-test",
            json={"contexts": [4096]},
        ) as r:
            assert r.status_code == 200
            saw_error = False
            for line in r.iter_lines():
                if line and line.startswith("data:") and "ollama_unreachable" in line:
                    saw_error = True
                    break
            assert saw_error


def test_perf_test_with_prior_on_demand_placement_drops_after(settings: Settings, seeded_users: dict) -> None:
    """When prior placement is on_demand, perf-test's restore stage calls
    `_drop_model` instead of warming back up — exercises that branch."""
    chat = _make_perf_chat()
    client = _build_client(
        settings,
        chat=chat,
        telemetry=FakeTelemetry(snapshots=[gpu_snapshot(0)]),
        skip_samplers=True,
    )

    # Pre-seed prior placement = on_demand.
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            session.add(ModelConfig(model="m", placement="on_demand"))
            session.commit()
    finally:
        engine.dispose()

    with client:
        _seed_gpu_state(client, gpu_count=1)
        _login_admin(client, seeded_users)
        with client.stream(
            "POST",
            "/api/admin/ollama/models/m/perf-test",
            json={"contexts": [4096]},
        ) as r:
            assert r.status_code == 200
            stages = []
            for line in r.iter_lines():
                if line and line.startswith("data:"):
                    payload = json.loads(line[len("data:") :].strip())
                    if "name" in payload:
                        stages.append(payload["name"])
            assert "restore" in stages


def test_dashboard_snapshot_exposes_latest_perf_per_benchmark_profile(
    session_factory: sessionmaker,
) -> None:
    state = ModelStateSamplerState(available_models=[model_info("m")])
    gpu_state = GpuSamplerState(last_snapshots=[gpu_snapshot(0), gpu_snapshot(1)])
    with session_factory() as session:
        session.add(
            ModelPerf(
                model="m",
                cold_load_seconds=3.0,
                warm_load_seconds=0.4,
                throughput_tps=30.0,
                max_ctx_observed=16384,
                benchmark_profile="gpu0",
                placement_tested="gpu0",
            )
        )
        session.add(
            ModelPerf(
                model="m",
                cold_load_seconds=4.0,
                warm_load_seconds=0.5,
                throughput_tps=40.0,
                max_ctx_observed=32768,
                benchmark_profile="multi_gpu",
                placement_tested="multi_gpu",
            )
        )
        session.commit()

        payload = assemble_dashboard_snapshot(
            session=session,
            gpu_state=gpu_state,
            model_state=state,
            now=1.0,
        )

    profiles = payload["models"][0]["benchmark_profiles"]
    assert {p["benchmark_profile"] for p in profiles} == {"gpu0", "multi_gpu"}
    assert payload["models"][0]["metrics"]["warm_load_seconds"] in {0.4, 0.5}
    assert all(p["recommendations"] for p in profiles)
    chat_rec = next(r for r in profiles[0]["recommendations"] if r["use_case"] == "chat")
    assert {"score", "confidence", "reasons", "warnings"} <= set(chat_rec)


def test_dashboard_snapshot_exposes_benchmark_history_staleness_and_drift(
    session_factory: sessionmaker,
) -> None:
    state = ModelStateSamplerState(available_models=[model_info("m")])
    gpu_state = GpuSamplerState(last_snapshots=[gpu_snapshot(0)])
    older = datetime.now(UTC) - timedelta(days=40)
    latest = datetime.now(UTC) - timedelta(days=35)
    with session_factory() as session:
        session.add(
            ModelPerf(
                model="m",
                measured_at=older,
                cold_load_seconds=10.0,
                warm_load_seconds=1.0,
                throughput_tps=40.0,
                max_ctx_observed=32768,
                benchmark_profile="gpu0",
                placement_tested="gpu0",
            )
        )
        session.add(
            ModelPerf(
                model="m",
                measured_at=latest,
                cold_load_seconds=16.0,
                warm_load_seconds=1.8,
                throughput_tps=25.0,
                max_ctx_observed=16384,
                benchmark_profile="gpu0",
                placement_tested="gpu0",
                notes="partial run: context probe stopped early",
            )
        )
        session.commit()

        payload = assemble_dashboard_snapshot(
            session=session,
            gpu_state=gpu_state,
            model_state=state,
            now=1.0,
        )

    profile = payload["models"][0]["benchmark_profiles"][0]
    assert profile["staleness"] == "old"
    assert profile["is_stale"] is True
    assert profile["drift_status"] == "warning"
    assert profile["trend_status"] == "unknown"
    assert profile["profile_status"] == "success"
    assert profile["data_quality"] in {"complete", "uncertain"}
    assert profile["retest_recommended"] is True
    assert len(profile["history"]) == 2
    assert any("tokens/s" in signal for signal in profile["drift_signals"])
    assert any("max context fell" in signal for signal in profile["drift_signals"])
    rec = next(r for r in profile["recommendations"] if r["use_case"] == "chat")
    assert rec["confidence"] == "low"
    assert any("old" in warning for warning in rec["warnings"])
    assert any("tokens/s" in warning for warning in rec["warnings"])


def test_dashboard_snapshot_trend_uses_recent_history_median(
    session_factory: sessionmaker,
) -> None:
    state = ModelStateSamplerState(available_models=[model_info("m")])
    gpu_state = GpuSamplerState(last_snapshots=[gpu_snapshot(0)])
    now = datetime.now(UTC)
    runs = [
        (now - timedelta(days=4), 40.0, 1.1, 8.0, 32768),
        (now - timedelta(days=3), 42.0, 1.0, 8.5, 32768),
        (now - timedelta(days=2), 39.0, 1.2, 8.0, 32768),
        (now - timedelta(days=1), 24.0, 2.2, 14.0, 16384),
    ]
    with session_factory() as session:
        for measured_at, tps, warm, cold, ctx in runs:
            session.add(
                ModelPerf(
                    model="m",
                    measured_at=measured_at,
                    cold_load_seconds=cold,
                    warm_load_seconds=warm,
                    throughput_tps=tps,
                    max_ctx_observed=ctx,
                    benchmark_profile="gpu0",
                    placement_tested="gpu0",
                    gpu_layout_json=json.dumps({"gpu0_vram_growth_mb": 1000}),
                )
            )
        session.commit()

        payload = assemble_dashboard_snapshot(
            session=session,
            gpu_state=gpu_state,
            model_state=state,
            now=1.0,
        )

    profile = payload["models"][0]["benchmark_profiles"][0]
    assert profile["trend_status"] == "warning"
    assert profile["trends"]["throughput_tps"]["direction"] == "down"
    assert profile["trends"]["warm_load_seconds"]["direction"] == "up"
    assert any(
        "trend" in warning
        for rec in profile["recommendations"]
        for warning in rec["warnings"]
    )


def test_recommendation_scoring_is_explainable_and_confidence_aware() -> None:
    recs = score_recommendations(
        model_name="qwen3-coder:30b",
        tag="code",
        metadata={
            "architecture_context_length": 65536,
            "capabilities": ["tools"],
        },
        metrics={
            "throughput_tps": 32.0,
            "warm_load_seconds": 1.2,
            "cold_load_seconds": 8.0,
            "max_ctx_observed": 65536,
            "benchmark_profile": "multi_gpu",
            "gpu_layout_diff": {"gpu0_vram_growth_mb": 8000, "gpu1_vram_growth_mb": 7000},
        },
        size_bytes=16 * 1024**3,
    )
    by_case = {r["use_case"]: r for r in recs}
    assert by_case["code"]["score"] >= 70
    assert by_case["code"]["confidence"] in {"medium", "high"}
    assert any("model tag" in reason for reason in by_case["code"]["reasons"])
    assert by_case["multi_gpu"]["score"] >= 70
    assert any("multiple GPUs" in reason for reason in by_case["multi_gpu"]["reasons"])


def test_recommendation_scoring_marks_missing_data_insufficient() -> None:
    recs = score_recommendations(
        model_name="tiny",
        tag=None,
        metadata={},
        metrics={
            "throughput_tps": None,
            "warm_load_seconds": None,
            "cold_load_seconds": None,
            "max_ctx_observed": None,
            "benchmark_profile": "on_demand",
            "notes": "profile failed: upstream timeout",
        },
    )
    assert all(r["confidence"] == "insufficient" for r in recs)
    assert all(r["score"] <= 25 for r in recs)
    assert any("tokens/s not measured" in warning for warning in recs[0]["warnings"])


def test_detect_main_gpu_actual_returns_none_for_empty_telemetry() -> None:
    from cockpit.routers.admin_ollama import _detect_main_gpu_actual

    assert _detect_main_gpu_actual(None, None) is None
    assert _detect_main_gpu_actual([], []) is None


def test_detect_main_gpu_actual_returns_none_when_no_growth() -> None:
    from cockpit.routers.admin_ollama import _detect_main_gpu_actual

    before = [gpu_snapshot(0, vram_used_mb=1000), gpu_snapshot(1, vram_used_mb=1000)]
    after = [gpu_snapshot(0, vram_used_mb=1000), gpu_snapshot(1, vram_used_mb=900)]
    assert _detect_main_gpu_actual(before, after) is None


def test_options_for_placement_table() -> None:
    """All four placement → options rows from the spec table."""
    from cockpit.routers.admin_ollama import _options_for_placement

    assert _options_for_placement("gpu2") == {"keep_alive": 24 * 3600, "main_gpu": 2}
    assert _options_for_placement("multi_gpu") == {"keep_alive": 24 * 3600, "num_gpu": 99}
    assert _options_for_placement("on_demand") == {"keep_alive": 0}
    assert _options_for_placement("available") == {"keep_alive": 0}


def test_allowed_placements_no_gpu() -> None:
    from cockpit.routers.admin_ollama import _allowed_placements

    assert _allowed_placements(0) == ["on_demand"]
    assert _allowed_placements(1) == ["gpu0", "on_demand"]


def test_parse_ollama_catalog_filters_installed_models() -> None:
    from cockpit.adapters.ollama_catalog import parse_ollama_catalog

    html = """
    <ul>
      <li x-test-model>
        <a href="/library/qwen3">
          <h2><span x-test-search-response-title>qwen3</span></h2>
          <p>Qwen model family.</p>
          <span x-test-capability>tools</span>
          <span x-test-size>8b</span>
          <span x-test-pull-count>10M</span>
          <span x-test-tag-count>58</span>
          <span x-test-updated>1 week ago</span>
        </a>
      </li>
      <li x-test-model>
        <a href="/library/gemma3">
          <h2><span x-test-search-response-title>gemma3</span></h2>
          <p>Gemma model family.</p>
          <span x-test-capability>vision</span>
          <span x-test-size>4b</span>
        </a>
      </li>
    </ul>
    """

    rows = parse_ollama_catalog(html, installed={"qwen3", "qwen3:8b"}, limit=10)

    assert [row["name"] for row in rows] == ["gemma3"]
    assert rows[0]["description"] == "Gemma model family."
    assert rows[0]["sizes"] == ["4b"]
    assert rows[0]["capabilities"] == ["vision"]
    assert rows[0]["url"] == "https://ollama.com/library/gemma3"


def test_ollama_show_parser_tolerates_model_metadata() -> None:
    from cockpit.adapters.ollama_chat import _parse_model_details

    details = _parse_model_details(
        "qwen3:8b",
        {
            "details": {
                "parameter_size": "8B",
                "quantization_level": "Q4_K_M",
            },
            "model_info": {"qwen2.context_length": 131072},
            "capabilities": ["completion", "tools"],
            "modified_at": "2026-05-01T10:00:00Z",
        },
    )
    assert details.parameter_size == "8B"
    assert details.quantization_level == "Q4_K_M"
    assert details.architecture_context_length == 131072
    assert details.capabilities == ["completion", "tools"]


# --- Direct unit tests for the perf-harness helpers -----------------------


@pytest.mark.asyncio
async def test_drop_model_swallows_model_not_found() -> None:
    from cockpit.routers.admin_ollama import _drop_model

    chat = FakeLLMChat(known_models={"only-known"})  # any other name → 404
    # Should not raise.
    await _drop_model(chat, "ghost")


@pytest.mark.asyncio
async def test_measure_throughput_returns_none_when_no_usage() -> None:
    from cockpit.routers.admin_ollama import _measure_throughput

    # Final chunk lacks usage / duration data.
    bad_final = ChatChunk(delta="", done=True)
    chat = FakeLLMChat(models=[model_info("m")], tokens=["x"], final_chunk=bad_final)
    assert await _measure_throughput(chat, "m") is None


@pytest.mark.asyncio
async def test_measure_throughput_computes_tps() -> None:
    from cockpit.routers.admin_ollama import _measure_throughput

    final = ChatChunk(
        delta="",
        done=True,
        usage_in=200,
        usage_out=200,
        eval_duration_ns=1_000_000_000,  # 1s
    )
    chat = FakeLLMChat(models=[model_info("m")], tokens=["x"], final_chunk=final)
    assert await _measure_throughput(chat, "m") == 200.0


@pytest.mark.asyncio
async def test_probe_max_context_returns_first_success_largest_first() -> None:
    from cockpit.routers.admin_ollama import _probe_max_context

    chat = FakeLLMChat(models=[model_info("m")], tokens=["ok"])
    out = await _probe_max_context(chat, "m", [4096, 16384, 32768])
    assert out == 32768  # largest tried first; succeeds


@pytest.mark.asyncio
async def test_probe_max_context_skips_failed_contexts() -> None:
    """If a context size fails, walk down the list."""
    from cockpit.ports.llm_chat import OllamaResponseError as ORE
    from cockpit.routers.admin_ollama import _probe_max_context

    class FailingChat(FakeLLMChat):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._tries = 0

        async def chat_stream(self, **kwargs):
            self._tries += 1
            if kwargs.get("options", {}).get("num_ctx", 0) > 16384:
                raise ORE(500, "context too big")
            yield ChatChunk(delta="ok", done=True, usage_out=1, eval_duration_ns=1_000_000_000)

    chat = FailingChat(models=[model_info("m")])
    out = await _probe_max_context(chat, "m", [4096, 16384, 32768, 65536])
    assert out == 16384


@pytest.mark.asyncio
async def test_probe_max_context_returns_none_when_model_not_found() -> None:
    from cockpit.routers.admin_ollama import _probe_max_context

    chat = FakeLLMChat(known_models={"only-known"})
    out = await _probe_max_context(chat, "ghost", [4096])
    assert out is None


def test_gpu_layout_diff_empty_returns_empty_dict() -> None:
    from cockpit.routers.admin_ollama import _gpu_layout_diff

    assert _gpu_layout_diff(None, None) == {}
    assert _gpu_layout_diff([], []) == {}


def test_last_perf_row_returns_none_when_no_row(
    session_factory: sessionmaker,
) -> None:
    from cockpit.routers.admin_ollama import _last_perf_row

    with session_factory() as session:
        assert _last_perf_row(session, "no-such-model") is None


@pytest.mark.asyncio
async def test_wait_loaded_returns_false_on_unreachable() -> None:
    """_wait_loaded handles OllamaUnreachableError by returning False fast."""
    from cockpit.routers.admin_ollama import _wait_loaded

    class UnreachableLoadChat(FakeLLMChat):
        async def loaded(self):
            raise OllamaUnreachableError("simulated")

    chat = UnreachableLoadChat()
    assert await _wait_loaded(chat, "m", timeout_s=1.0) is False


@pytest.mark.asyncio
async def test_wait_unloaded_returns_true_on_unreachable() -> None:
    """If Ollama is unreachable while waiting for unload, treat as 'gone'."""
    from cockpit.routers.admin_ollama import _wait_unloaded

    class UnreachableLoadChat(FakeLLMChat):
        async def loaded(self):
            raise OllamaUnreachableError("simulated")

    chat = UnreachableLoadChat()
    assert await _wait_unloaded(chat, "m", timeout_s=1.0) is True


@pytest.mark.asyncio
async def test_wait_loaded_returns_false_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If `loaded()` never lists the model, the timeout fires and we return False."""
    from cockpit.routers import admin_ollama as ao

    monkeypatch.setattr(ao, "LOADED_POLL_INTERVAL_S", 0.001)
    chat = FakeLLMChat(loaded=[])  # never includes "m"
    assert await ao._wait_loaded(chat, "m", timeout_s=0.01) is False


@pytest.mark.asyncio
async def test_wait_unloaded_returns_false_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cockpit.routers import admin_ollama as ao

    monkeypatch.setattr(ao, "LOADED_POLL_INTERVAL_S", 0.001)
    # `loaded()` always lists "m" → never unloads → timeout
    chat = FakeLLMChat(loaded=[LoadedModel(name="m", size_vram=1, until=None)])
    assert await ao._wait_unloaded(chat, "m", timeout_s=0.01) is False


def test_replace_existing_config_uses_else_branch(settings: Settings, seeded_users: dict) -> None:
    """First place creates a new model_config row; second place updates the
    existing row — exercises `_upsert_model_config`'s else branch (line 212)."""
    chat = FakeLLMChat(
        models=[model_info("m")],
        loaded=[LoadedModel(name="m", size_vram=1, until=None)],
        tokens=["ok"],
    )
    client = _build_client(
        settings,
        chat=chat,
        telemetry=FakeTelemetry(snapshots=[gpu_snapshot(0)]),
        skip_samplers=True,
    )
    with client:
        _seed_gpu_state(client, gpu_count=1)
        _login_admin(client, seeded_users)
        client.post("/api/admin/ollama/models/m/place", json={"placement": "gpu0"})
        # Re-place — now the config row already exists.
        r = client.post("/api/admin/ollama/models/m/place", json={"placement": "on_demand"})
    assert r.status_code == 200
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            cfg = session.query(ModelConfig).filter_by(model="m").one()
            assert cfg.placement == "on_demand"
    finally:
        engine.dispose()


# =========================================================================
# Coverage: services/metrics.py run() loop cancellation
# =========================================================================


@pytest.mark.asyncio
async def test_gpu_sampler_run_loop_cancels_cleanly(
    session_factory: sessionmaker,
) -> None:
    """The periodic loop runs at least one iteration and exits cleanly when
    cancelled — exercises the CancelledError handler."""
    state = GpuSamplerState()
    telemetry = FakeTelemetry(snapshots=[gpu_snapshot(0)])
    sampler = GpuSampler(
        telemetry=telemetry,
        session_factory=session_factory,
        state=state,
        interval_s=0.01,  # tight loop for the test
    )
    task = asyncio.create_task(sampler.run())
    # Yield long enough for at least one iteration.
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert state.last_snapshots is not None  # at least one sample landed


@pytest.mark.asyncio
async def test_model_state_sampler_run_loop_cancels_cleanly() -> None:
    state = ModelStateSamplerState()
    chat = FakeLLMChat(models=[model_info("m")])
    sampler = ModelStateSampler(chat=chat, state=state, interval_s=0.01)
    task = asyncio.create_task(sampler.run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert state.last_success_at is not None


@pytest.mark.asyncio
async def test_gpu_sampler_handles_unexpected_exception(
    session_factory: sessionmaker,
) -> None:
    """`Telemetry.sample()` raising something not in the documented
    exception set should be caught by the defensive `except Exception`
    block, logged, and the sampler should keep running."""

    class BoomTelemetry(FakeTelemetry):
        async def sample(self):
            raise RuntimeError("explosion")

    state = GpuSamplerState()
    sampler = GpuSampler(
        telemetry=BoomTelemetry(),
        session_factory=session_factory,
        state=state,
    )
    await sampler.sample_once()
    assert state.last_error is not None
    assert "RuntimeError" in state.last_error


@pytest.mark.asyncio
async def test_model_state_sampler_handles_unexpected_exception() -> None:
    class BoomChat(FakeLLMChat):
        async def list_models(self):
            raise RuntimeError("explosion")

    state = ModelStateSamplerState()
    sampler = ModelStateSampler(chat=BoomChat(), state=state)
    await sampler.sample_once()
    assert state.last_error is not None
    assert "RuntimeError" in state.last_error


@pytest.mark.asyncio
async def test_gpu_sampler_run_loop_handles_runtime_exception(
    session_factory: sessionmaker,
) -> None:
    """run()'s outer except Exception swallows things that escape sample_once."""

    class FlakySampler(GpuSampler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._n = 0

        async def sample_once(self):
            self._n += 1
            if self._n == 1:
                raise RuntimeError("escaped")
            await super().sample_once()

    state = GpuSamplerState()
    sampler = FlakySampler(
        telemetry=FakeTelemetry(snapshots=[gpu_snapshot(0)]),
        session_factory=session_factory,
        state=state,
        interval_s=0.01,
    )
    task = asyncio.create_task(sampler.run())
    await asyncio.sleep(0.06)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert state.last_snapshots is not None  # second iteration succeeded


@pytest.mark.asyncio
async def test_model_state_sampler_run_loop_handles_runtime_exception() -> None:
    class FlakySampler(ModelStateSampler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._n = 0

        async def sample_once(self):
            self._n += 1
            if self._n == 1:
                raise RuntimeError("escaped")
            await super().sample_once()

    state = ModelStateSamplerState()
    sampler = FlakySampler(
        chat=FakeLLMChat(models=[model_info("m")]),
        state=state,
        interval_s=0.01,
    )
    task = asyncio.create_task(sampler.run())
    await asyncio.sleep(0.06)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert state.last_success_at is not None


@pytest.mark.asyncio
async def test_assemble_dashboard_snapshot_with_no_models(
    session_factory: sessionmaker,
) -> None:
    """Empty model list → empty cards + empty configs/tags lookup paths."""
    g = GpuSamplerState(last_snapshots=[gpu_snapshot(0)], last_success_at=1.0)
    m = ModelStateSamplerState(
        available_models=[],
        loaded_models=[],
        last_success_at=1.0,
    )
    with session_factory() as session:
        payload = assemble_dashboard_snapshot(
            session=session, gpu_state=g, model_state=m, last_calls=None, now=2.0
        )
    snap = DashboardSnapshot.model_validate(payload)
    assert snap.models == []
    assert snap.gpus == [
        # one GPU, one snapshot
        snap.gpus[0]
    ]


_ = Any  # quiet unused-import lint
_ = datetime  # used by perf-test fakes
