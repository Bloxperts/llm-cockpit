"""Dashboard metrics services.

Two periodic samplers + a pure snapshot assembler:

- `GpuSampler` — every 5 s. Calls `Telemetry.sample()`, persists rows to
  `metrics_snapshot`, and updates `app.state.gpu_snapshots` so the
  dashboard endpoints can return the latest reading without hitting the
  subprocess on every request.
- `ModelStateSampler` — every 30 s. Calls `LLMChat.list_models()` and
  `LLMChat.loaded()` and updates `app.state.model_state`.
- `assemble_dashboard_snapshot(...)` — pure function that takes the
  current app.state inputs + a session and returns the dashboard payload
  dict that matches the schema in the UC-02 functional spec.

The samplers expose `sample_once()` for tests + `run()` for the lifespan
loop. Both methods swallow exceptions: a single failed iteration logs and
continues so the loop is fault-tolerant.
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func as sqlfunc
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from cockpit.models import (
    Conversation,
    Message,
    MetricsSnapshot,
    ModelConfig,
    ModelMetadata,
    ModelPerf,
    ModelTag,
)
from cockpit.ports.llm_chat import (
    LLMChat,
    LoadedModel,
    ModelInfo,
    OllamaResponseError,
    OllamaUnreachableError,
)
from cockpit.ports.telemetry import GpuSnapshot, Telemetry, TelemetryUnavailableError
from cockpit.services.recommendations import score_recommendations

log = logging.getLogger(__name__)

GPU_SAMPLE_INTERVAL_S = 5.0
MODEL_STATE_SAMPLE_INTERVAL_S = 30.0
OLLAMA_UNREACHABLE_THRESHOLD_S = 30.0
BENCHMARK_HISTORY_LIMIT = 5
BENCHMARK_STALE_DAYS = 14.0
BENCHMARK_OLD_DAYS = 30.0
TREND_MIN_BASELINE_RUNS = 2


# --- Per-app state containers --------------------------------------------


@dataclass
class GpuSamplerState:
    last_snapshots: list[GpuSnapshot] | None = None
    last_success_at: float | None = None
    last_error: str | None = None
    last_error_at: float | None = None


@dataclass
class ModelStateSamplerState:
    available_models: list[ModelInfo] = dc_field(default_factory=list)
    loaded_models: list[LoadedModel] = dc_field(default_factory=list)
    last_success_at: float | None = None
    last_error: str | None = None
    last_error_at: float | None = None


# --- Samplers ------------------------------------------------------------


class GpuSampler:
    """Polls `Telemetry.sample()` on a fixed cadence; persists `MetricsSnapshot`
    rows; keeps the most recent snapshot list on `state.last_snapshots`.

    Constructor takes a `session_factory` so each iteration owns its own
    session — long-lived sessions across the lifespan of the app are a
    SQLAlchemy anti-pattern.
    """

    def __init__(
        self,
        *,
        telemetry: Telemetry,
        session_factory: sessionmaker[Session],
        state: GpuSamplerState,
        interval_s: float = GPU_SAMPLE_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.telemetry = telemetry
        self.session_factory = session_factory
        self.state = state
        self.interval_s = interval_s
        self._clock = clock

    async def sample_once(self) -> None:
        """Run a single sample iteration. Persist on success, log on failure."""
        try:
            snapshots = await self.telemetry.sample()
        except TelemetryUnavailableError as exc:
            self.state.last_error = str(exc)
            self.state.last_error_at = self._clock()
            log.warning("GpuSampler: telemetry unavailable: %s", exc)
            return
        except Exception as exc:  # defensive — never crash the loop
            self.state.last_error = f"{type(exc).__name__}: {exc}"
            self.state.last_error_at = self._clock()
            log.warning("GpuSampler: unexpected error: %s", exc)
            return

        self.state.last_snapshots = snapshots
        self.state.last_success_at = self._clock()
        self.state.last_error = None

        if snapshots is None:
            return  # no GPU; nothing to persist

        with self.session_factory() as session:
            for snap in snapshots:
                session.add(
                    MetricsSnapshot(
                        gpu_index=snap.index,
                        vram_used_mb=snap.vram_used_mb,
                        vram_total_mb=snap.vram_total_mb,
                        temp_c=snap.temp_c,
                        power_w=snap.power_w,
                    )
                )
            session.commit()

    async def run(self) -> None:
        """Periodic loop. Cancelled cleanly when the lifespan exits."""
        while True:
            try:
                await self.sample_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # belt + braces over sample_once's own try/except
                log.warning("GpuSampler.run: %s", exc)
            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                raise


class ModelStateSampler:
    """Polls `LLMChat.list_models()` + `LLMChat.loaded()` on a fixed cadence
    so `/api/dashboard/snapshot` doesn't hit Ollama on every request.

    Errors set `state.last_error` and `state.last_error_at`; the dashboard
    surfaces this as the `ollama_unreachable` status.

    UC-10 — when the available-model list changes (new model name
    appears that we haven't seen before), the sampler triggers
    `model_tags.reapply_heuristics()` so the new model gets a tag row
    inserted automatically. Override rows are never touched. Pass
    `session_factory=None` to disable this side effect (the v0.1
    bootstrap path doesn't need it).
    """

    def __init__(
        self,
        *,
        chat: LLMChat,
        state: ModelStateSamplerState,
        interval_s: float = MODEL_STATE_SAMPLE_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.chat = chat
        self.state = state
        self.interval_s = interval_s
        self._clock = clock
        self.session_factory = session_factory
        # Set of model names we've already evaluated; populated lazily.
        self._known_names: set[str] = set()

    async def sample_once(self) -> None:
        try:
            available = await self.chat.list_models()
            loaded = await self.chat.loaded()
        except (OllamaUnreachableError, OllamaResponseError) as exc:
            self.state.last_error = str(exc)
            self.state.last_error_at = self._clock()
            log.warning("ModelStateSampler: %s", exc)
            return
        except Exception as exc:
            self.state.last_error = f"{type(exc).__name__}: {exc}"
            self.state.last_error_at = self._clock()
            log.warning("ModelStateSampler: unexpected error: %s", exc)
            return

        self.state.available_models = available
        self.state.loaded_models = loaded
        self.state.last_success_at = self._clock()
        self.state.last_error = None

        # UC-10: reapply heuristics when the model list grows. We only
        # care about new names — disappeared rows aren't deleted from
        # `model_tags` (an admin who pulls a model back later wants
        # their override to survive).
        if self.session_factory is not None:
            current_names = {m.name for m in available}
            new_names = current_names - self._known_names
            if new_names:
                # Lazy import to keep `services/metrics.py` self-contained
                # for tests that don't need the heuristic side effect.
                from cockpit.services.model_tags import reapply_heuristics

                try:
                    with self.session_factory() as session:
                        reapply_heuristics(session, sorted(current_names))
                        session.commit()
                except Exception as exc:  # pragma: no cover — defensive
                    log.warning(
                        "ModelStateSampler: heuristic reapply failed: %s", exc
                    )
                self._known_names = current_names

    async def run(self) -> None:
        while True:
            try:
                await self.sample_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("ModelStateSampler.run: %s", exc)
            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                raise


# --- Snapshot assembler --------------------------------------------------


def _columns_for(gpu_count: int) -> list[str]:
    """Build the placement column list per UC-02 §placement board.

    No GPU → no GPU columns and no Cross GPU column (nothing to span).
    1 GPU → gpu0 only (no Cross GPU; only one GPU).
    ≥ 2 GPUs → gpu0..gpuN + Cross GPU (`multi_gpu` on the wire).
    Always: On Demand.
    """
    cols: list[str] = []
    cols.extend(f"gpu{i}" for i in range(gpu_count))
    if gpu_count >= 2:
        cols.append("multi_gpu")
    cols.append("on_demand")
    return cols


def _dashboard_placement(placement: str | None) -> str:
    """Collapse legacy/unconfigured rows into the visible cold bucket."""
    if placement in (None, "available"):
        return "on_demand"
    return placement


def _model_state_status(
    gpu_state: GpuSamplerState,
    model_state: ModelStateSamplerState,
    *,
    now: float,
) -> str:
    """Per UC-02 functional spec §status field.

    Three states:
        healthy             — both samplers have at least one successful run
                              and no ongoing error.
        ollama_unreachable  — ModelStateSampler has been failing > 30 s.
        degraded            — anything else (e.g. GPU sampler errored once,
                              model sampler is fine).
    """
    model_failing_for = 0.0
    if model_state.last_error is not None and model_state.last_error_at is not None:
        # Failing window: from last_error_at to now, *if* there hasn't been a
        # success since.
        if (
            model_state.last_success_at is None
            or model_state.last_success_at < model_state.last_error_at
        ):
            model_failing_for = now - model_state.last_error_at
    if model_failing_for > OLLAMA_UNREACHABLE_THRESHOLD_S:
        return "ollama_unreachable"

    if gpu_state.last_error is not None or model_state.last_error is not None:
        return "degraded"
    if model_state.last_success_at is None:
        # Never succeeded — treat as degraded (we don't know yet).
        return "degraded"
    return "healthy"


def _serialize_gpu(snap: GpuSnapshot) -> dict[str, Any]:
    return {
        "index": snap.index,
        "vram_used_mb": snap.vram_used_mb,
        "vram_total_mb": snap.vram_total_mb,
        "temp_c": snap.temp_c,
        "power_w": snap.power_w,
        # Sprint 5b: configured power cap, used by the dashboard's
        # watts-vs-TDP display.
        "max_power_w": snap.max_power_w,
    }


def _serialize_loaded(loaded: list[LoadedModel]) -> dict[str, dict[str, Any]]:
    return {
        m.name: {
            "loaded": True,
            "vram_mb": m.size_vram // (1024 * 1024) if m.size_vram else None,
            "until": m.until.isoformat() if m.until is not None else None,
        }
        for m in loaded
    }


def _perf_profile(row: ModelPerf) -> str:
    return row.benchmark_profile or row.placement_tested or "on_demand"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _benchmark_age_days(row: ModelPerf, *, now: datetime) -> float | None:
    measured_at = _as_utc(row.measured_at)
    if measured_at is None:
        return None
    return max(0.0, (now - measured_at).total_seconds() / 86400)


def _pct_change(current: float | int | None, baseline: float | int | None) -> float | None:
    if current is None or baseline is None or baseline == 0:
        return None
    return (float(current) - float(baseline)) / float(baseline)


def _profile_status(row: ModelPerf) -> str:
    notes = (row.notes or "").lower()
    if "skipped" in notes:
        return "skipped"
    if "failed" in notes or "error" in notes or "not_supported" in notes:
        return "failed"
    facts = [
        row.cold_load_seconds,
        row.warm_load_seconds,
        row.throughput_tps,
        row.max_ctx_observed,
    ]
    if all(value is not None for value in facts):
        return "success"
    if any(value is not None for value in facts):
        return "partial"
    return "incomplete"


def _data_quality(row: ModelPerf) -> str:
    status = _profile_status(row)
    if status in {"failed", "skipped", "incomplete"}:
        return "insufficient"
    if status == "partial":
        return "partial"
    if not row.gpu_layout_json and _perf_profile(row) != "on_demand":
        return "uncertain"
    return "complete"


def _numeric_values(rows: list[ModelPerf], field: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = getattr(row, field)
        if value is not None:
            values.append(float(value))
    return values


def _trend_for_metric(
    latest: ModelPerf,
    baseline_rows: list[ModelPerf],
    field: str,
    *,
    higher_is_better: bool,
    threshold: float,
) -> dict[str, Any]:
    latest_value = getattr(latest, field)
    baseline_values = _numeric_values(baseline_rows, field)
    if latest_value is None or len(baseline_values) < TREND_MIN_BASELINE_RUNS:
        return {
            "direction": "unknown",
            "pct_change": None,
            "quality": "missing",
            "latest": latest_value,
            "baseline": None,
        }
    baseline = statistics.median(baseline_values)
    change = _pct_change(latest_value, baseline)
    if change is None:
        direction = "unknown"
    elif abs(change) < threshold:
        direction = "flat"
    elif change > 0:
        direction = "up"
    else:
        direction = "down"

    quality = "ok"
    if len(baseline_values) >= 3:
        median = baseline or 0
        spread = (max(baseline_values) - min(baseline_values)) / median if median else 0
        if spread >= 0.60:
            quality = "volatile"

    if direction in {"up", "down"}:
        bad = (direction == "down" and higher_is_better) or (direction == "up" and not higher_is_better)
        if bad and quality == "ok":
            quality = "ok"
    return {
        "direction": direction,
        "pct_change": change,
        "quality": quality,
        "latest": float(latest_value),
        "baseline": baseline,
    }


def _trend_summary(latest: ModelPerf, history_rows: list[ModelPerf]) -> dict[str, Any]:
    baseline_rows = history_rows[1:BENCHMARK_HISTORY_LIMIT]
    trends = {
        "throughput_tps": _trend_for_metric(
            latest,
            baseline_rows,
            "throughput_tps",
            higher_is_better=True,
            threshold=0.20,
        ),
        "warm_load_seconds": _trend_for_metric(
            latest,
            baseline_rows,
            "warm_load_seconds",
            higher_is_better=False,
            threshold=0.30,
        ),
        "cold_load_seconds": _trend_for_metric(
            latest,
            baseline_rows,
            "cold_load_seconds",
            higher_is_better=False,
            threshold=0.30,
        ),
        "max_ctx_observed": _trend_for_metric(
            latest,
            baseline_rows,
            "max_ctx_observed",
            higher_is_better=True,
            threshold=0.20,
        ),
    }
    signals: list[str] = []
    status = "stable"
    if all(trend["direction"] == "unknown" for trend in trends.values()):
        return {
            "trend_status": "unknown",
            "trend_signals": ["not enough history for trend detection"],
            "trends": trends,
        }
    labels = {
        "throughput_tps": "tokens/s",
        "warm_load_seconds": "warm load",
        "cold_load_seconds": "cold load",
        "max_ctx_observed": "max context",
    }
    higher_is_better = {
        "throughput_tps": True,
        "warm_load_seconds": False,
        "cold_load_seconds": False,
        "max_ctx_observed": True,
    }
    for field, trend in trends.items():
        direction = trend["direction"]
        if trend["quality"] == "volatile":
            status = "unknown" if status == "stable" else status
            signals.append(f"{labels[field]} history is volatile")
            continue
        if direction == "unknown":
            continue
        if direction == "flat":
            continue
        bad = (direction == "down" and higher_is_better[field]) or (
            direction == "up" and not higher_is_better[field]
        )
        pct = trend["pct_change"]
        pct_label = f"{abs(pct) * 100:.0f}%" if isinstance(pct, int | float) else "changed"
        if bad:
            status = "warning"
            signals.append(f"{labels[field]} trend worsened by {pct_label}")
        elif status == "stable":
            status = "info"
            signals.append(f"{labels[field]} trend improved by {pct_label}")
    if not baseline_rows:
        return {
            "trend_status": "unknown",
            "trend_signals": ["not enough history for trend detection"],
            "trends": trends,
        }
    if not signals:
        signals.append("recent trend is within conservative thresholds")
    return {"trend_status": status, "trend_signals": signals, "trends": trends}


def _drift_summary(latest: ModelPerf, previous: ModelPerf | None) -> dict[str, Any]:
    if previous is None:
        return {
            "drift_status": "unknown",
            "drift_signals": ["not enough history for drift detection"],
        }

    signals: list[str] = []
    status = "stable"

    tps_change = _pct_change(latest.throughput_tps, previous.throughput_tps)
    if tps_change is not None and abs(tps_change) >= 0.25:
        direction = "slower" if tps_change < 0 else "faster"
        signals.append(f"tokens/s {abs(tps_change) * 100:.0f}% {direction} than previous run")
        status = "warning" if tps_change < 0 else "info"

    for metric_field, label in (
        ("cold_load_seconds", "cold load"),
        ("warm_load_seconds", "warm load"),
    ):
        load_change = _pct_change(getattr(latest, metric_field), getattr(previous, metric_field))
        if load_change is not None and abs(load_change) >= 0.40:
            direction = "slower" if load_change > 0 else "faster"
            signals.append(f"{label} {abs(load_change) * 100:.0f}% {direction} than previous run")
            if load_change > 0:
                status = "warning"
            elif status == "stable":
                status = "info"

    ctx_change = _pct_change(latest.max_ctx_observed, previous.max_ctx_observed)
    if ctx_change is not None and latest.max_ctx_observed is not None and ctx_change < 0:
        signals.append(
            f"max context fell from {int(previous.max_ctx_observed or 0):,} to {latest.max_ctx_observed:,}"
        )
        status = "warning"

    if not signals:
        signals.append("latest run is within conservative drift thresholds")
    return {"drift_status": status, "drift_signals": signals}


def _history_entry(row: ModelPerf, *, now: datetime) -> dict[str, Any]:
    age_days = _benchmark_age_days(row, now=now)
    return {
        "measured_at": row.measured_at.isoformat() if row.measured_at else None,
        "cold_load_seconds": row.cold_load_seconds,
        "warm_load_seconds": row.warm_load_seconds,
        "throughput_tps": row.throughput_tps,
        "max_ctx_observed": row.max_ctx_observed,
        "notes": row.notes,
        "age_days": age_days,
        "status": _profile_status(row),
    }


def _serialize_perf(
    row: ModelPerf,
    *,
    history_rows: list[ModelPerf] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    history_rows = history_rows or [row]
    previous = history_rows[1] if len(history_rows) > 1 else None
    age_days = _benchmark_age_days(row, now=now)
    drift = _drift_summary(row, previous)
    trend = _trend_summary(row, history_rows)
    profile_status = _profile_status(row)
    data_quality = _data_quality(row)
    staleness = (
        "old"
        if age_days is not None and age_days >= BENCHMARK_OLD_DAYS
        else "stale"
        if age_days is not None and age_days >= BENCHMARK_STALE_DAYS
        else "fresh"
        if age_days is not None
        else "unknown"
    )
    retest_reasons: list[str] = []
    if profile_status in {"failed", "skipped", "partial", "incomplete"}:
        retest_reasons.append(f"profile {profile_status}")
    if staleness in {"stale", "old"}:
        retest_reasons.append(f"benchmark {staleness}")
    if drift["drift_status"] == "warning":
        retest_reasons.append("drift warning")
    if trend["trend_status"] == "warning":
        retest_reasons.append("trend warning")
    return {
        "cold_load_seconds": row.cold_load_seconds,
        "warm_load_seconds": row.warm_load_seconds,
        "throughput_tps": row.throughput_tps,
        "max_ctx_observed": row.max_ctx_observed,
        "benchmark_profile": _perf_profile(row),
        "placement_tested": row.placement_tested,
        "call_count": row.call_count,
        "gpu_layout_diff": json.loads(row.gpu_layout_json) if row.gpu_layout_json else {},
        "notes": row.notes,
        "recommendations": [],
        "measured_at": row.measured_at.isoformat() if row.measured_at else None,
        "age_days": age_days,
        "is_stale": bool(age_days is not None and age_days >= BENCHMARK_STALE_DAYS),
        "staleness": staleness,
        "drift_status": drift["drift_status"],
        "drift_signals": drift["drift_signals"],
        "trend_status": trend["trend_status"],
        "trend_signals": trend["trend_signals"],
        "trends": trend["trends"],
        "profile_status": profile_status,
        "data_quality": data_quality,
        "retest_recommended": bool(retest_reasons),
        "retest_reason": ", ".join(retest_reasons) if retest_reasons else None,
        "history": [_history_entry(item, now=now) for item in history_rows[:BENCHMARK_HISTORY_LIMIT]],
    }


def _latest_perf_profiles_for(
    session: Session,
    model: str,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    rows = (
        session.execute(
            select(ModelPerf)
            .where(ModelPerf.model == model)
            .order_by(ModelPerf.measured_at.desc(), ModelPerf.id.desc())
        )
        .scalars()
        .all()
    )
    seen: set[str] = set()
    history_by_profile: dict[str, list[ModelPerf]] = {}
    for row in rows:
        history_by_profile.setdefault(_perf_profile(row), []).append(row)
    latest: list[dict[str, Any]] = []
    for row in rows:
        profile = _perf_profile(row)
        if profile in seen:
            continue
        seen.add(profile)
        latest.append(_serialize_perf(row, history_rows=history_by_profile[profile], now=now))
    return latest


def _last_perf_for(
    session: Session,
    model: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    now = now or datetime.now(UTC)
    rows = (
        session.execute(
            select(ModelPerf)
            .where(ModelPerf.model == model)
            .order_by(ModelPerf.measured_at.desc(), ModelPerf.id.desc())
            .limit(BENCHMARK_HISTORY_LIMIT)
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    return _serialize_perf(rows[0], history_rows=rows, now=now)


def _keep_alive_label(config: ModelConfig | None) -> str:
    mode = config.keep_alive_mode if config is not None else "default"
    seconds = config.keep_alive_seconds if config is not None else None
    if mode == "permanent":
        return "Permanent"
    if mode == "unload":
        return "Unload"
    if mode == "finite" and seconds is not None:
        if seconds % 3600 == 0:
            return f"{seconds // 3600}h"
        if seconds % 60 == 0:
            return f"{seconds // 60}m"
        return f"{seconds}s"
    return "Default"


def _metadata_payload(metadata: ModelMetadata | None, info: ModelInfo) -> dict[str, Any]:
    capabilities: list[str] = []
    if metadata is not None and metadata.capabilities_json:
        try:
            parsed = json.loads(metadata.capabilities_json)
            if isinstance(parsed, list):
                capabilities = [str(item) for item in parsed]
        except json.JSONDecodeError:
            capabilities = []

    release_label = "Release: unknown"
    release_date = None
    if metadata is not None:
        if metadata.release_date is not None:
            release_date = metadata.release_date.date().isoformat()
            release_label = f"Released: {release_date}"
        elif metadata.registry_updated_at is not None:
            release_date = metadata.registry_updated_at.date().isoformat()
            release_label = f"Updated: {release_date}"
        elif metadata.local_modified_at is not None:
            release_date = metadata.local_modified_at.date().isoformat()
            release_label = f"Local: {release_date}"
    else:
        release_date = info.modified.date().isoformat()
        release_label = f"Local: {release_date}"

    return {
        "parameter_size": metadata.parameter_size if metadata is not None else None,
        "quantization_level": metadata.quantization_level if metadata is not None else None,
        "architecture_context_length": (
            metadata.architecture_context_length if metadata is not None else None
        ),
        "release_date": release_date,
        "release_date_label": release_label,
        "capabilities": capabilities,
    }


def _context_payload(
    *,
    config: ModelConfig | None,
    metadata: ModelMetadata | None,
    perf: dict[str, Any] | None,
    loaded_info: dict[str, Any] | None,
    gpus: list[GpuSnapshot],
) -> dict[str, Any]:
    measured = perf.get("max_ctx_observed") if perf else None
    if not loaded_info or not gpus or metadata is None or metadata.architecture_context_length is None:
        return {
            "max_estimated_ctx": None,
            "max_measured_ctx": measured,
            "estimate_confidence": "measured" if measured else "unknown",
            "headroom_mb": None,
        }

    total_mb = sum(g.vram_total_mb for g in gpus)
    free_mb = sum(max(0, g.vram_total_mb - g.vram_used_mb) for g in gpus)
    headroom_mb = max(1024 * len(gpus), int(total_mb * 0.15))
    usable_mb = max(0, free_mb - headroom_mb)
    # Pragmatic first estimate: roughly 2 MiB/token for KV/cache growth on
    # larger local models. It is intentionally conservative until measured.
    estimated = min(metadata.architecture_context_length, int(usable_mb / 2))
    if config is not None and config.num_ctx_default is not None:
        estimated = max(estimated, min(config.num_ctx_default, metadata.architecture_context_length))
    return {
        "max_estimated_ctx": estimated if estimated > 0 else None,
        "max_measured_ctx": measured,
        "estimate_confidence": "measured" if measured else "estimated",
        "headroom_mb": headroom_mb,
    }


def _build_model_card(
    *,
    info: ModelInfo,
    config: ModelConfig | None,
    tag: str | None,
    tag_source: str | None,
    loaded_index: dict[str, dict[str, Any]],
    perf: dict[str, Any] | None,
    benchmark_profiles: list[dict[str, Any]],
    metadata: ModelMetadata | None,
    calls_30d: int,
    gpus: list[GpuSnapshot],
) -> dict[str, Any]:
    placement = _dashboard_placement(config.placement if config is not None else None)
    metadata_payload = _metadata_payload(metadata, info)
    for row in benchmark_profiles:
        row["recommendations"] = score_recommendations(
            model_name=info.name,
            tag=tag,
            metadata=metadata_payload,
            metrics=row,
            size_bytes=info.size_bytes,
        )
    if perf is not None:
        perf["recommendations"] = score_recommendations(
            model_name=info.name,
            tag=tag,
            metadata=metadata_payload,
            metrics=perf,
            size_bytes=info.size_bytes,
        )
    config_payload = {
        "placement": placement,
        "keep_alive_mode": config.keep_alive_mode if config is not None else "default",
        "keep_alive_seconds": config.keep_alive_seconds if config is not None else None,
        "keep_alive_label": _keep_alive_label(config),
        "num_ctx_default": config.num_ctx_default if config is not None else None,
        "single_flight": bool(config.single_flight) if config is not None else False,
    }
    loaded_info = loaded_index.get(info.name)
    actual = {
        "loaded": bool(loaded_info),
        "vram_mb": (loaded_info or {}).get("vram_mb"),
        "expires_at": (loaded_info or {}).get("until"),
        "main_gpu_actual": None,  # set by placement-transition handler when known
        "gpu_layout": None,
        "mismatch": False,
    }
    return {
        "name": info.name,
        "tag": tag,
        "tag_source": tag_source,
        "size_bytes": info.size_bytes,
        "calls_30d": calls_30d,
        "metadata": metadata_payload,
        "config": config_payload,
        "actual": actual,
        "context": _context_payload(
            config=config,
            metadata=metadata,
            perf=perf,
            loaded_info=loaded_info,
            gpus=gpus,
        ),
        "metrics": perf,
        "benchmark_profiles": benchmark_profiles,
    }


def assemble_dashboard_snapshot(
    *,
    session: Session,
    gpu_state: GpuSamplerState,
    model_state: ModelStateSamplerState,
    last_calls: list[dict[str, Any]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Pure-function snapshot assembler. The pre-computed sampler state
    means we avoid hitting Ollama / the GPU on each HTTP request.

    `last_calls` is `[]` until UC-04 lands the chat router writing the
    `messages` table — see TODO comment in routers/dashboard.py.
    """
    now = now if now is not None else time.monotonic()
    wall_now = datetime.now(UTC)
    db_cutoff_30d = wall_now.replace(tzinfo=None) - timedelta(days=30)
    snapshots = gpu_state.last_snapshots or []
    gpus_payload = [_serialize_gpu(s) for s in snapshots]
    columns = _columns_for(len(snapshots))

    loaded_index = _serialize_loaded(model_state.loaded_models)

    # Pull per-model auxiliary data from the DB in one shot.
    model_names = [info.name for info in model_state.available_models]
    if model_names:
        configs = {
            cfg.model: cfg
            for cfg in session.execute(
                select(ModelConfig).where(ModelConfig.model.in_(model_names))
            ).scalars()
        }
        tags = {
            tag.model: (tag.tag, tag.source)
            for tag in session.execute(
                select(ModelTag).where(ModelTag.model.in_(model_names))
            ).scalars()
        }
        metadata = {
            meta.model: meta
            for meta in session.execute(
                select(ModelMetadata).where(ModelMetadata.model.in_(model_names))
            ).scalars()
        }
        call_model = sqlfunc.coalesce(Message.model, Conversation.model).label("call_model")
        calls_30d = {
            row.call_model: int(row.calls)
            for row in session.execute(
                select(call_model, sqlfunc.count(Message.id).label("calls"))
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(
                    Message.role == "user",
                    call_model.in_(model_names),
                    Message.ts >= db_cutoff_30d,
                )
                .group_by(call_model)
            )
        }
        perf_calls_30d = {
            row.model: int(row.calls)
            for row in session.execute(
                select(ModelPerf.model, sqlfunc.sum(ModelPerf.call_count).label("calls"))
                .where(
                    ModelPerf.model.in_(model_names),
                    ModelPerf.measured_at >= db_cutoff_30d,
                )
                .group_by(ModelPerf.model)
            )
        }
        for model, count in perf_calls_30d.items():
            calls_30d[model] = calls_30d.get(model, 0) + count
    else:
        configs = {}
        tags = {}
        metadata = {}
        calls_30d = {}

    models_payload = [
        _build_model_card(
            info=info,
            config=configs.get(info.name),
            tag=tags.get(info.name, (None, None))[0],
            tag_source=tags.get(info.name, (None, None))[1],
            loaded_index=loaded_index,
            perf=_last_perf_for(session, info.name, now=wall_now),
            benchmark_profiles=_latest_perf_profiles_for(session, info.name, now=wall_now),
            metadata=metadata.get(info.name),
            calls_30d=calls_30d.get(info.name, 0),
            gpus=snapshots,
        )
        for info in model_state.available_models
    ]

    return {
        "gpus": gpus_payload,
        "columns": columns,
        "models": models_payload,
        "last_calls": list(last_calls) if last_calls is not None else [],
        "status": _model_state_status(gpu_state, model_state, now=now),
        "ts": datetime.now(UTC).isoformat(),
    }
