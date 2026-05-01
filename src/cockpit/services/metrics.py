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
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from cockpit.models import MetricsSnapshot, ModelConfig, ModelMetadata, ModelPerf, ModelTag
from cockpit.ports.llm_chat import (
    LLMChat,
    LoadedModel,
    ModelInfo,
    OllamaResponseError,
    OllamaUnreachableError,
)
from cockpit.ports.telemetry import GpuSnapshot, Telemetry, TelemetryUnavailableError

log = logging.getLogger(__name__)

GPU_SAMPLE_INTERVAL_S = 5.0
MODEL_STATE_SAMPLE_INTERVAL_S = 30.0
OLLAMA_UNREACHABLE_THRESHOLD_S = 30.0


# --- Per-app state containers --------------------------------------------


@dataclass
class GpuSamplerState:
    last_snapshots: list[GpuSnapshot] | None = None
    last_success_at: float | None = None
    last_error: str | None = None
    last_error_at: float | None = None


@dataclass
class ModelStateSamplerState:
    available_models: list[ModelInfo] = field(default_factory=list)
    loaded_models: list[LoadedModel] = field(default_factory=list)
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
    Always: On Demand + Available.
    """
    cols: list[str] = []
    cols.extend(f"gpu{i}" for i in range(gpu_count))
    if gpu_count >= 2:
        cols.append("multi_gpu")
    cols.append("on_demand")
    cols.append("available")
    return cols


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


def _last_perf_for(session: Session, model: str) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(ModelPerf)
            .where(ModelPerf.model == model)
            .order_by(ModelPerf.measured_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        return None
    return {
        "cold_load_seconds": row.cold_load_seconds,
        "throughput_tps": row.throughput_tps,
        "max_ctx_observed": row.max_ctx_observed,
        "placement_tested": row.placement_tested,
        "measured_at": row.measured_at.isoformat() if row.measured_at else None,
    }


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
    metadata: ModelMetadata | None,
    gpus: list[GpuSnapshot],
) -> dict[str, Any]:
    placement = config.placement if config is not None else "available"
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
        "main_gpu_actual": None,  # set by placement-transition handler when known
        "gpu_layout": None,
        "mismatch": False,
    }
    return {
        "name": info.name,
        "tag": tag,
        "tag_source": tag_source,
        "size_bytes": info.size_bytes,
        "metadata": _metadata_payload(metadata, info),
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
    else:
        configs = {}
        tags = {}
        metadata = {}

    models_payload = [
        _build_model_card(
            info=info,
            config=configs.get(info.name),
            tag=tags.get(info.name, (None, None))[0],
            tag_source=tags.get(info.name, (None, None))[1],
            loaded_index=loaded_index,
            perf=_last_perf_for(session, info.name),
            metadata=metadata.get(info.name),
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
        "ts": datetime.now(timezone.utc).isoformat(),
    }
