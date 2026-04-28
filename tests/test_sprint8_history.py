"""Sprint 8 — UC-03 dashboard history tests.

Two test groups, both backend-only:

    services/aggregator.py — MinuteAggregator + HourAggregator
    routers/dashboard_history.py — GET /api/dashboard/history

The aggregator tests insert fixed rows into `metrics_snapshot` and
`metrics_snapshot_minute`, drive `aggregate_once()` directly with a
pinned `now` clock, and assert the produced bucket rows are correct
+ the upsert is idempotent + retention pruning fires.

The history-endpoint tests round-trip via FastAPI TestClient against
an in-memory SQLite seeded with synthetic minute/hour rows for the GPU
metrics and assistant `messages` rows for calls / latency / tokens.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from cockpit.adapters.fake_chat import FakeLLMChat
from cockpit.adapters.fake_telemetry import FakeTelemetry
from cockpit.config import Settings
from cockpit.db import make_engine, make_session_factory, upgrade_to_head
from cockpit.main import create_app
from cockpit.models import (
    Conversation,
    Message,
    MetricsSnapshot,
    MetricsSnapshotHour,
    MetricsSnapshotMinute,
    User,
)
from cockpit.routers.dashboard_history import _percentile
from cockpit.services.aggregator import HourAggregator, MinuteAggregator
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
def seeded_user(settings: Settings) -> dict:
    """One settled chat user named 'alice' so the auth gate is satisfied."""
    engine = make_engine(settings.db_url)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            session.add(
                User(
                    username="alice",
                    pw_hash=hash_password("AlicePW01!", cost=settings.bcrypt_cost),
                    role="chat",
                    must_change_password=0,
                )
            )
            session.commit()
    finally:
        engine.dispose()
    return {"alice": {"password": "AlicePW01!"}}


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


# =========================================================================
# Migration round-trip
# =========================================================================


def test_migration_0005_creates_tables_and_supports_upsert(
    session_factory: sessionmaker,
) -> None:
    """The new tables exist, the unique constraint fires on (bucket_ts,
    gpu_index) duplicates, and INSERT OR IGNORE is the right pattern."""
    bucket = datetime(2026, 4, 28, 12, 30, 0)

    with session_factory() as session:
        session.execute(
            text(
                """
                INSERT OR IGNORE INTO metrics_snapshot_minute
                    (bucket_ts, gpu_index, vram_used_mb_avg,
                     temp_c_avg, temp_c_max, power_w_avg, sample_count)
                VALUES (:b, 0, 1234.0, 60.0, 65.0, 200.0, 12)
                """
            ),
            {"b": bucket},
        )
        session.execute(
            text(
                """
                INSERT OR IGNORE INTO metrics_snapshot_minute
                    (bucket_ts, gpu_index, vram_used_mb_avg,
                     temp_c_avg, temp_c_max, power_w_avg, sample_count)
                VALUES (:b, 0, 9999.0, 99.0, 99.0, 999.0, 999)
                """
            ),
            {"b": bucket},
        )
        session.commit()
        rows = (
            session.execute(select(MetricsSnapshotMinute))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].vram_used_mb_avg == 1234.0
    assert rows[0].sample_count == 12


# =========================================================================
# MinuteAggregator
# =========================================================================


def _insert_metrics_snapshot(
    session_factory: sessionmaker,
    *,
    ts: datetime,
    gpu_index: int,
    vram_mb: int = 5000,
    temp_c: float = 60.0,
    power_w: float = 200.0,
) -> None:
    with session_factory() as session:
        session.add(
            MetricsSnapshot(
                ts=ts,
                gpu_index=gpu_index,
                vram_used_mb=vram_mb,
                vram_total_mb=24000,
                temp_c=temp_c,
                power_w=power_w,
            )
        )
        session.commit()


def test_minute_aggregator_buckets_correct_average(
    session_factory: sessionmaker,
) -> None:
    """Five samples in the closed minute → one row per gpu_index with
    averages and counts. Two GPUs."""
    minute_start = datetime(2026, 4, 28, 12, 30, 0)
    # Five samples each, both GPUs — within [12:30, 12:31).
    for i in range(5):
        ts = minute_start + timedelta(seconds=i * 10)
        _insert_metrics_snapshot(
            session_factory, ts=ts, gpu_index=0, vram_mb=4000 + i * 100, temp_c=50 + i
        )
        _insert_metrics_snapshot(
            session_factory,
            ts=ts,
            gpu_index=1,
            vram_mb=8000 + i * 100,
            temp_c=70 + i,
        )

    agg = MinuteAggregator(
        session_factory=session_factory,
        clock=lambda: datetime(2026, 4, 28, 12, 31, 30),
    )
    agg.aggregate_once()

    with session_factory() as session:
        rows = (
            session.execute(
                select(MetricsSnapshotMinute).order_by(
                    MetricsSnapshotMinute.gpu_index
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2
    gpu0, gpu1 = rows
    assert gpu0.gpu_index == 0
    assert gpu0.bucket_ts == minute_start
    assert gpu0.sample_count == 5
    assert abs(gpu0.vram_used_mb_avg - 4200.0) < 0.01
    assert abs(gpu0.temp_c_avg - 52.0) < 0.01
    assert gpu0.temp_c_max == 54.0
    assert gpu1.gpu_index == 1
    assert gpu1.sample_count == 5
    assert abs(gpu1.vram_used_mb_avg - 8200.0) < 0.01


def test_minute_aggregator_idempotent(session_factory: sessionmaker) -> None:
    minute_start = datetime(2026, 4, 28, 12, 30, 0)
    for i in range(3):
        _insert_metrics_snapshot(
            session_factory,
            ts=minute_start + timedelta(seconds=i * 10),
            gpu_index=0,
        )
    agg = MinuteAggregator(
        session_factory=session_factory,
        clock=lambda: datetime(2026, 4, 28, 12, 31, 30),
    )
    agg.aggregate_once()
    agg.aggregate_once()
    agg.aggregate_once()
    with session_factory() as session:
        rows = (
            session.execute(select(MetricsSnapshotMinute)).scalars().all()
        )
    assert len(rows) == 1


def test_minute_aggregator_skips_in_progress_minute(
    session_factory: sessionmaker,
) -> None:
    """The current (open) minute is left alone — only the most recent
    closed minute is aggregated."""
    closed_minute = datetime(2026, 4, 28, 12, 30, 0)
    open_minute = datetime(2026, 4, 28, 12, 31, 0)
    _insert_metrics_snapshot(
        session_factory, ts=closed_minute + timedelta(seconds=20), gpu_index=0
    )
    _insert_metrics_snapshot(
        session_factory, ts=open_minute + timedelta(seconds=20), gpu_index=0
    )

    agg = MinuteAggregator(
        session_factory=session_factory,
        clock=lambda: datetime(2026, 4, 28, 12, 31, 45),
    )
    agg.aggregate_once()
    with session_factory() as session:
        rows = (
            session.execute(select(MetricsSnapshotMinute)).scalars().all()
        )
    assert len(rows) == 1
    assert rows[0].bucket_ts == closed_minute
    assert rows[0].sample_count == 1


def test_minute_aggregator_handles_empty_window(
    session_factory: sessionmaker,
) -> None:
    """No raw rows in the closed minute → no bucket inserted, no error."""
    agg = MinuteAggregator(
        session_factory=session_factory,
        clock=lambda: datetime(2026, 4, 28, 12, 31, 30),
    )
    agg.aggregate_once()
    with session_factory() as session:
        rows = (
            session.execute(select(MetricsSnapshotMinute)).scalars().all()
        )
    assert rows == []


def test_minute_aggregator_prunes_old_raw_rows(
    session_factory: sessionmaker,
) -> None:
    """Raw rows older than 7 d are removed; younger rows are kept."""
    now = datetime(2026, 4, 28, 12, 31, 30)
    _insert_metrics_snapshot(
        session_factory, ts=now - timedelta(days=8), gpu_index=0
    )
    _insert_metrics_snapshot(
        session_factory, ts=now - timedelta(days=2), gpu_index=0
    )
    agg = MinuteAggregator(session_factory=session_factory, clock=lambda: now)
    agg.aggregate_once()

    with session_factory() as session:
        kept = (
            session.execute(select(MetricsSnapshot)).scalars().all()
        )
    assert len(kept) == 1
    assert (now - kept[0].ts).days < 7


def test_minute_aggregator_swallows_db_errors(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A forced exception is logged; doesn't propagate."""

    def boom(*_a, **_kw):
        raise RuntimeError("forced")

    monkeypatch.setattr(MinuteAggregator, "_aggregate", staticmethod(boom))
    agg = MinuteAggregator(session_factory=session_factory)
    agg.aggregate_once()  # no exception escapes


# =========================================================================
# HourAggregator
# =========================================================================


def _insert_minute_bucket(
    session_factory: sessionmaker,
    *,
    bucket_ts: datetime,
    gpu_index: int = 0,
    vram_avg: float = 5000.0,
    temp_avg: float = 60.0,
    temp_max: float | None = None,
    sample_count: int = 12,
) -> None:
    with session_factory() as session:
        session.add(
            MetricsSnapshotMinute(
                bucket_ts=bucket_ts,
                gpu_index=gpu_index,
                vram_used_mb_avg=vram_avg,
                temp_c_avg=temp_avg,
                temp_c_max=temp_max if temp_max is not None else temp_avg,
                power_w_avg=200.0,
                sample_count=sample_count,
            )
        )
        session.commit()


def test_hour_aggregator_buckets_from_minute_table(
    session_factory: sessionmaker,
) -> None:
    """Insert minute rows across one hour → one hour-row per gpu_index
    with the average of the minute averages and the max of the maxes."""
    hour_start = datetime(2026, 4, 28, 12, 0, 0)
    for m in range(60):
        ts = hour_start + timedelta(minutes=m)
        _insert_minute_bucket(
            session_factory,
            bucket_ts=ts,
            gpu_index=0,
            vram_avg=5000.0 + m,
            temp_avg=50.0 + m * 0.1,
            temp_max=51.0 + m * 0.1,
        )

    agg = HourAggregator(
        session_factory=session_factory,
        clock=lambda: datetime(2026, 4, 28, 13, 30, 0),
    )
    agg.aggregate_once()

    with session_factory() as session:
        rows = (
            session.execute(select(MetricsSnapshotHour)).scalars().all()
        )
    assert len(rows) == 1
    h = rows[0]
    assert h.bucket_ts == hour_start
    assert h.gpu_index == 0
    # Average of 5000..5059 inclusive = 5029.5
    assert abs(h.vram_used_mb_avg - 5029.5) < 0.01
    assert h.sample_count == 60 * 12  # SUM of minute sample_counts


def test_hour_aggregator_prunes_old_minute_rows(
    session_factory: sessionmaker,
) -> None:
    """Minute rows older than 30 d are removed."""
    now = datetime(2026, 4, 28, 13, 30, 0)
    _insert_minute_bucket(
        session_factory, bucket_ts=now - timedelta(days=31), gpu_index=0
    )
    _insert_minute_bucket(
        session_factory, bucket_ts=now - timedelta(days=10), gpu_index=0
    )
    agg = HourAggregator(session_factory=session_factory, clock=lambda: now)
    agg.aggregate_once()
    with session_factory() as session:
        kept = (
            session.execute(select(MetricsSnapshotMinute)).scalars().all()
        )
    assert len(kept) == 1
    assert (now - kept[0].bucket_ts).days < 30


def test_hour_aggregator_swallows_db_errors(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a, **_kw):
        raise RuntimeError("forced")

    monkeypatch.setattr(HourAggregator, "_aggregate", staticmethod(boom))
    agg = HourAggregator(session_factory=session_factory)
    agg.aggregate_once()


# =========================================================================
# Periodic loop coverage (mirrors the GpuSampler.run pattern in UC-02 tests)
# =========================================================================


@pytest.mark.asyncio
async def test_minute_aggregator_run_loop_cancels_cleanly(
    session_factory: sessionmaker,
) -> None:
    """At least one iteration runs, then a Cancel exits the loop cleanly."""
    agg = MinuteAggregator(
        session_factory=session_factory,
        interval_s=0.01,
    )
    task = asyncio.create_task(agg.run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_hour_aggregator_run_loop_cancels_cleanly(
    session_factory: sessionmaker,
) -> None:
    agg = HourAggregator(
        session_factory=session_factory,
        interval_s=0.01,
    )
    task = asyncio.create_task(agg.run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_minute_aggregator_run_loop_handles_runtime_exception(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A defensive try/except in `run()` swallows runtime exceptions
    raised inside `aggregate_once()` so the loop keeps cycling."""
    calls = {"n": 0}

    def boom(self):
        calls["n"] += 1
        raise RuntimeError("forced")

    monkeypatch.setattr(MinuteAggregator, "aggregate_once", boom)
    agg = MinuteAggregator(session_factory=session_factory, interval_s=0.01)
    task = asyncio.create_task(agg.run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert calls["n"] >= 1


@pytest.mark.asyncio
async def test_hour_aggregator_run_loop_handles_runtime_exception(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def boom(self):
        calls["n"] += 1
        raise RuntimeError("forced")

    monkeypatch.setattr(HourAggregator, "aggregate_once", boom)
    agg = HourAggregator(session_factory=session_factory, interval_s=0.01)
    task = asyncio.create_task(agg.run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert calls["n"] >= 1


# =========================================================================
# Percentile helper
# =========================================================================


def test_percentile_basic() -> None:
    assert _percentile([], 50) is None
    assert _percentile([100.0], 50) == 100.0
    # Median of 1..5 is 3 with linear interpolation.
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0
    # p95 of 1..100 is interpolated near 95.05.
    p95 = _percentile([float(x) for x in range(1, 101)], 95)
    assert p95 is not None and 94.5 <= p95 <= 95.5


# =========================================================================
# History endpoint
# =========================================================================


def _seed_minute_buckets(
    session_factory: sessionmaker,
    *,
    now: datetime,
    n_minutes: int = 5,
    gpu_indexes: tuple[int, ...] = (0, 1),
) -> None:
    """Insert n closed minute buckets ending at `now` for each GPU."""
    for m in range(n_minutes):
        ts = (now - timedelta(minutes=m + 1)).replace(second=0, microsecond=0)
        for gpu_index in gpu_indexes:
            _insert_minute_bucket(
                session_factory,
                bucket_ts=ts,
                gpu_index=gpu_index,
                vram_avg=5000.0 + 1000 * gpu_index,
                temp_avg=60.0 + gpu_index * 5,
                temp_max=65.0 + gpu_index * 5,
                sample_count=12,
            )


def _seed_hour_buckets(
    session_factory: sessionmaker,
    *,
    now: datetime,
    n_hours: int = 3,
    gpu_indexes: tuple[int, ...] = (0, 1),
) -> None:
    for h in range(n_hours):
        ts = (now - timedelta(hours=h + 1)).replace(
            minute=0, second=0, microsecond=0
        )
        for gpu_index in gpu_indexes:
            with session_factory() as session:
                session.add(
                    MetricsSnapshotHour(
                        bucket_ts=ts,
                        gpu_index=gpu_index,
                        vram_used_mb_avg=10000.0 + 1000 * gpu_index,
                        temp_c_avg=70.0 + gpu_index * 3,
                        temp_c_max=75.0 + gpu_index * 3,
                        power_w_avg=250.0,
                        sample_count=720,
                    )
                )
                session.commit()


def _seed_assistant_messages(
    session_factory: sessionmaker,
    *,
    timestamps: list[datetime],
    latencies: list[int] | None = None,
    usage_in: int = 100,
    usage_out: int = 50,
) -> None:
    """Add a chat conversation owned by alice (id=1) plus N assistant
    messages with the given timestamps. `latencies` is matched 1-1."""
    if latencies is None:
        latencies = [200] * len(timestamps)
    with session_factory() as session:
        conv = Conversation(user_id=1, mode="chat", model="llama3:8b")
        session.add(conv)
        session.flush()
        for ts, lat in zip(timestamps, latencies, strict=True):
            session.add(
                Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content="hi",
                    model="llama3:8b",
                    usage_in=usage_in,
                    usage_out=usage_out,
                    gen_tps=42.0,
                    latency_ms=lat,
                    ts=ts,
                )
            )
        session.commit()


def test_history_requires_auth(settings: Settings) -> None:
    client = _build_client(settings)
    with client:
        r = client.get("/api/dashboard/history?range=24h&metric=gpu_temp")
    assert r.status_code == 401


def test_history_rejects_invalid_range(settings: Settings, seeded_user: dict) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded_user, "alice")
        r = client.get("/api/dashboard/history?range=99h&metric=gpu_temp")
    assert r.status_code == 422


def test_history_rejects_invalid_metric(
    settings: Settings, seeded_user: dict
) -> None:
    client = _build_client(settings)
    with client:
        _login(client, seeded_user, "alice")
        r = client.get("/api/dashboard/history?range=24h&metric=foo")
    assert r.status_code == 422


def test_history_gpu_temp_24h_returns_per_gpu_series(
    settings: Settings, seeded_user: dict, session_factory: sessionmaker
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_minute_buckets(session_factory, now=now, n_minutes=5)

    client = _build_client(settings)
    with client:
        _login(client, seeded_user, "alice")
        r = client.get("/api/dashboard/history?range=24h&metric=gpu_temp")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["series"]) == 2
    labels = {s["label"] for s in body["series"]}
    assert labels == {"GPU 0", "GPU 1"}
    for series in body["series"]:
        assert len(series["data"]) == 5
        for pt in series["data"]:
            assert "ts" in pt and "value" in pt


def test_history_gpu_temp_7d_uses_hour_table(
    settings: Settings, seeded_user: dict, session_factory: sessionmaker
) -> None:
    """Ensure range=7d reads from the hour table, not the minute table."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_minute_buckets(session_factory, now=now)  # populated but not used
    _seed_hour_buckets(session_factory, now=now, n_hours=3)

    client = _build_client(settings)
    with client:
        _login(client, seeded_user, "alice")
        r = client.get("/api/dashboard/history?range=7d&metric=gpu_temp")
    assert r.status_code == 200
    body = r.json()
    # 3 hour buckets × 2 GPUs.
    total = sum(len(s["data"]) for s in body["series"])
    assert total == 6


def test_history_vram_24h_returns_per_gpu_series(
    settings: Settings, seeded_user: dict, session_factory: sessionmaker
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_minute_buckets(session_factory, now=now, n_minutes=3)

    client = _build_client(settings)
    with client:
        _login(client, seeded_user, "alice")
        r = client.get("/api/dashboard/history?range=24h&metric=vram")
    assert r.status_code == 200
    body = r.json()
    assert len(body["series"]) == 2
    # Values should be the vram_used_mb_avg seeds (5000 / 6000), not temps.
    gpu0_vals = [
        pt["value"] for s in body["series"] if s["label"] == "GPU 0" for pt in s["data"]
    ]
    assert all(v == 5000.0 for v in gpu0_vals)


def test_history_calls_24h_minute_buckets(
    settings: Settings, seeded_user: dict, session_factory: sessionmaker
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    base = now.replace(second=0, microsecond=0) - timedelta(minutes=10)
    # Three minutes × varying call counts.
    _seed_assistant_messages(
        session_factory,
        timestamps=[
            base, base, base,                    # bucket 0: 3 calls
            base + timedelta(minutes=1),         # bucket 1: 1 call
            base + timedelta(minutes=2),         # bucket 2: 2 calls
            base + timedelta(minutes=2, seconds=30),
        ],
    )
    client = _build_client(settings)
    with client:
        _login(client, seeded_user, "alice")
        r = client.get("/api/dashboard/history?range=24h&metric=calls")
    assert r.status_code == 200
    body = r.json()
    assert len(body["series"]) == 1
    counts = [pt["value"] for pt in body["series"][0]["data"]]
    assert counts == [3.0, 1.0, 2.0]


def test_history_calls_excludes_user_and_system_rows(
    settings: Settings, seeded_user: dict, session_factory: sessionmaker
) -> None:
    """User + system messages must not be counted."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    base = now.replace(second=0, microsecond=0) - timedelta(minutes=5)
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
                    ts=base,
                    latency_ms=100,
                )
            )
        session.commit()

    client = _build_client(settings)
    with client:
        _login(client, seeded_user, "alice")
        r = client.get("/api/dashboard/history?range=24h&metric=calls")
    body = r.json()
    counts = [pt["value"] for pt in body["series"][0]["data"]]
    assert counts == [1.0]  # only the assistant row


def test_history_latency_computes_p50_and_p95(
    settings: Settings, seeded_user: dict, session_factory: sessionmaker
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    base = now.replace(second=0, microsecond=0) - timedelta(minutes=5)
    # Twenty data points in the same bucket.
    latencies = [10 * i for i in range(1, 21)]  # 10..200
    _seed_assistant_messages(
        session_factory,
        timestamps=[base] * 20,
        latencies=latencies,
    )
    client = _build_client(settings)
    with client:
        _login(client, seeded_user, "alice")
        r = client.get("/api/dashboard/history?range=24h&metric=latency")
    assert r.status_code == 200
    body = r.json()
    labels = {s["label"] for s in body["series"]}
    assert labels == {"p50", "p95"}
    p50 = next(s for s in body["series"] if s["label"] == "p50")["data"][0]["value"]
    p95 = next(s for s in body["series"] if s["label"] == "p95")["data"][0]["value"]
    # Linear interpolation: p50 ≈ 105, p95 ≈ 191.
    assert 100 <= p50 <= 110
    assert 180 <= p95 <= 200


def test_history_latency_skips_null_rows(
    settings: Settings, seeded_user: dict, session_factory: sessionmaker
) -> None:
    """A latency_ms IS NULL row must not be sent through `_percentile`."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    base = now.replace(second=0, microsecond=0) - timedelta(minutes=2)
    with session_factory() as session:
        conv = Conversation(user_id=1, mode="chat", model="llama3:8b")
        session.add(conv)
        session.flush()
        session.add(
            Message(
                conversation_id=conv.id,
                role="assistant",
                content="ok",
                latency_ms=100,
                ts=base,
            )
        )
        session.add(
            Message(
                conversation_id=conv.id,
                role="assistant",
                content="oops",
                latency_ms=None,  # cancelled mid-stream
                ts=base,
            )
        )
        session.commit()

    client = _build_client(settings)
    with client:
        _login(client, seeded_user, "alice")
        r = client.get("/api/dashboard/history?range=24h&metric=latency")
    body = r.json()
    p50_data = next(
        s for s in body["series"] if s["label"] == "p50"
    )["data"]
    assert len(p50_data) == 1
    assert p50_data[0]["value"] == 100.0


def test_history_tokens_returns_input_and_output_series(
    settings: Settings, seeded_user: dict, session_factory: sessionmaker
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    base = now.replace(second=0, microsecond=0) - timedelta(minutes=3)
    _seed_assistant_messages(
        session_factory,
        timestamps=[base, base + timedelta(seconds=30)],
        usage_in=120,
        usage_out=60,
    )
    client = _build_client(settings)
    with client:
        _login(client, seeded_user, "alice")
        r = client.get("/api/dashboard/history?range=24h&metric=tokens")
    body = r.json()
    labels = {s["label"] for s in body["series"]}
    assert labels == {"Input tokens", "Output tokens"}
    in_total = sum(
        pt["value"]
        for s in body["series"]
        if s["label"] == "Input tokens"
        for pt in s["data"]
    )
    out_total = sum(
        pt["value"]
        for s in body["series"]
        if s["label"] == "Output tokens"
        for pt in s["data"]
    )
    assert in_total == 240
    assert out_total == 120


def test_history_calls_7d_hour_buckets(
    settings: Settings, seeded_user: dict, session_factory: sessionmaker
) -> None:
    """range=7d uses hourly strftime grouping ('%Y-%m-%dT%H:00:00')."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    hour_start = now.replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=2
    )
    # Three calls in one hour, one in another.
    _seed_assistant_messages(
        session_factory,
        timestamps=[
            hour_start + timedelta(minutes=10),
            hour_start + timedelta(minutes=20),
            hour_start + timedelta(minutes=30),
            hour_start + timedelta(hours=1, minutes=10),
        ],
    )
    client = _build_client(settings)
    with client:
        _login(client, seeded_user, "alice")
        r = client.get("/api/dashboard/history?range=7d&metric=calls")
    body = r.json()
    counts = [pt["value"] for pt in body["series"][0]["data"]]
    assert counts == [3.0, 1.0]


def test_history_empty_metrics_returns_empty_series_list(
    settings: Settings, seeded_user: dict
) -> None:
    """No data → empty series list, not 500."""
    client = _build_client(settings)
    with client:
        _login(client, seeded_user, "alice")
        r = client.get("/api/dashboard/history?range=24h&metric=gpu_temp")
    assert r.status_code == 200
    assert r.json() == {"series": []}


def test_history_partial_data_does_not_break_chart_shape(
    settings: Settings, seeded_user: dict, session_factory: sessionmaker
) -> None:
    """Only one GPU has bucket rows → one series, not two."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _seed_minute_buckets(session_factory, now=now, n_minutes=2, gpu_indexes=(0,))

    client = _build_client(settings)
    with client:
        _login(client, seeded_user, "alice")
        r = client.get("/api/dashboard/history?range=24h&metric=gpu_temp")
    body = r.json()
    assert len(body["series"]) == 1
    assert body["series"][0]["label"] == "GPU 0"
