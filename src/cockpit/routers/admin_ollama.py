"""Admin Ollama router — placement, perf-test, pull, delete, settings.

Per UC-02 functional spec §API + §Backend logic. Every endpoint here is
gated by `Depends(require_role("admin"))` (ADR-004 §2: admin is the only
rung that gets these capabilities in v0.1).

State-changing actions write one row to `admin_audit` per DP-013. Per-model
single-flight is enforced via `app.state.model_locks` (ADR-005 §5); the
perf harness additionally takes `app.state.host_perf_lock` so only one
perf test runs at a time across all models.
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from cockpit.deps import get_chat_factory, get_session, get_telemetry_factory
from cockpit.models import (
    Message,
    ModelConfig,
    ModelMetadata,
    ModelPerf,
    ModelTag,
    Setting,
    User,
)
from cockpit.ports.llm_chat import (
    LLMChat,
    OllamaModelNotFound,
    OllamaResponseError,
    OllamaUnreachableError,
)
from cockpit.ports.telemetry import GpuSnapshot, Telemetry
from cockpit.routers.auth import require_role
from cockpit.schemas import (
    ModelCallEntry,
    ModelMetricsDrilldown,
    ModelMetricsSummary,
    ModelSettingsPatch,
    ModelTagPatchRequest,
    ModelTagResponse,
    PerfTestRequest,
    PlaceApplied,
    PlaceRequest,
    PlaceResponse,
    SettingsPutRequest,
    SettingsPutResponse,
    SettingsResponse,
)
from cockpit.services.audit import write_admin_audit
from cockpit.services.model_tags import (
    SETTINGS_KEY_TAG_HEURISTICS,
    load_heuristic_from_yaml,
    reapply_heuristics,
)

log = logging.getLogger(__name__)
router = APIRouter()

# Spec values — UC-02 §placement transition.
PLACEMENT_KEEP_ALIVE_WARM_S = 24 * 3600  # 24h
PLACEMENT_KEEP_ALIVE_COLD_S = 0  # drop after the call
LOADED_CONFIRMATION_TIMEOUT_S = 10.0
LOADED_POLL_INTERVAL_S = 0.5

# Throughput probe — ADR-005 §4 step 2.
THROUGHPUT_PROMPT_TOKENS = 200
THROUGHPUT_OUTPUT_TOKENS = 200
THROUGHPUT_RUNS = 3

# Default context-probe ladder per the spec.
DEFAULT_CONTEXTS = [4096, 16384, 32768, 65536]
PERF_HEARTBEAT_INTERVAL_S = 1.0


class _PerfCancelled(Exception):
    """Internal control-flow signal for cooperative perf-test cancellation."""


@dataclass
class _PerfRunState:
    model: str
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    stage: str = "starting"
    started_at: float = field(default_factory=time.monotonic)
    last_event_at: float = field(default_factory=time.monotonic)

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)


# --- Placement helpers ----------------------------------------------------


def _detected_gpu_count(request: Request) -> int:
    snaps = request.app.state.gpu_state.last_snapshots or []
    return len(snaps)


def _allowed_placements(gpu_count: int) -> list[str]:
    cols: list[str] = [f"gpu{i}" for i in range(gpu_count)]
    if gpu_count >= 2:
        cols.append("multi_gpu")
    cols.extend(["on_demand", "available"])
    return cols


def _resolve_keep_alive(
    placement: str,
    *,
    keep_alive_mode: str | None = None,
    keep_alive_seconds: int | None = None,
) -> int:
    mode = keep_alive_mode or "default"
    if mode == "permanent":
        return -1
    if mode == "unload":
        return 0
    if mode == "finite" and keep_alive_seconds is not None:
        return max(0, int(keep_alive_seconds))
    if placement in ("on_demand", "available"):
        return PLACEMENT_KEEP_ALIVE_COLD_S
    return PLACEMENT_KEEP_ALIVE_WARM_S


def _options_for_placement(
    placement: str,
    *,
    keep_alive_mode: str | None = None,
    keep_alive_seconds: int | None = None,
    num_ctx_default: int | None = None,
) -> dict[str, Any]:
    """UC-02 §placement transition table.

    | placement      | keep_alive | main_gpu | num_gpu |
    |----------------|------------|----------|---------|
    | gpu0..gpuN     | 24h        | int      | omitted |
    | multi_gpu      | 24h        | omitted  | 99      |
    | on_demand      | 0          | omitted  | omitted |
    | available      | 0          | omitted  | omitted |
    """
    keep_alive = _resolve_keep_alive(
        placement,
        keep_alive_mode=keep_alive_mode,
        keep_alive_seconds=keep_alive_seconds,
    )
    if placement.startswith("gpu") and placement != "multi_gpu":
        gpu_idx = int(placement[3:])
        options: dict[str, Any] = {"keep_alive": keep_alive, "main_gpu": gpu_idx}
        if num_ctx_default is not None:
            options["num_ctx"] = num_ctx_default
        return options
    if placement == "multi_gpu":
        options = {"keep_alive": keep_alive, "num_gpu": 99}
        if num_ctx_default is not None:
            options["num_ctx"] = num_ctx_default
        return options
    # on_demand / available
    return {"keep_alive": PLACEMENT_KEEP_ALIVE_COLD_S}


def _expected_main_gpu(placement: str) -> int | None:
    if placement.startswith("gpu") and placement != "multi_gpu":
        return int(placement[3:])
    return None


def _placement_should_be_loaded(placement: str) -> bool:
    return placement.startswith("gpu") or placement == "multi_gpu"


# --- ad-hoc adapter context manager --------------------------------------


class _AdapterScope:
    """`async with` wrapper that builds an ad-hoc LLMChat / Telemetry adapter
    via the configured factory and `aclose()`s it on exit. Routers use this
    so the long-lived sampler adapters don't have to be reused for one-shot
    requests.
    """

    def __init__(self, build):
        self._build = build
        self._instance = None

    async def __aenter__(self):
        self._instance = self._build()
        return self._instance

    async def __aexit__(self, exc_type, exc, tb):
        aclose = getattr(self._instance, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                pass
        self._instance = None


# --- Placement endpoint ---------------------------------------------------


async def _warm_up(chat: LLMChat, model: str, options: dict[str, Any]) -> None:
    """Trigger the load with the right options. Discard output."""
    try:
        async for _chunk in chat.chat_stream(
            model=model,
            messages=[{"role": "user", "content": " "}],
            options=options,
        ):
            # We only care about the first chunk to confirm streaming started;
            # the warm-up is about making Ollama load the model with the
            # right keep_alive / main_gpu options, not about generating output.
            break
    except OllamaModelNotFound:
        # Surface upward — the route turns it into a 404.
        raise


async def _wait_loaded(chat: LLMChat, model: str, *, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            loaded = await chat.loaded()
        except (OllamaUnreachableError, OllamaResponseError):
            return False
        if any(m.name == model for m in loaded):
            return True
        await asyncio.sleep(LOADED_POLL_INTERVAL_S)
    return False


async def _wait_unloaded(chat: LLMChat, model: str, *, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            loaded = await chat.loaded()
        except (OllamaUnreachableError, OllamaResponseError):
            return True
        if not any(m.name == model for m in loaded):
            return True
        await asyncio.sleep(LOADED_POLL_INTERVAL_S)
    return False


def _detect_main_gpu_actual(before: list[GpuSnapshot] | None, after: list[GpuSnapshot] | None) -> int | None:
    """Compare pre/post snapshots — return the GPU index whose VRAM grew the
    most. Returns None if telemetry was unavailable or growth is ambiguous.
    """
    if not before or not after:
        return None
    before_by_idx = {s.index: s.vram_used_mb for s in before}
    growths = []
    for s in after:
        delta = s.vram_used_mb - before_by_idx.get(s.index, 0)
        if delta > 0:
            growths.append((delta, s.index))
    if not growths:
        return None
    growths.sort(reverse=True)  # largest delta first
    return growths[0][1]


def _upsert_model_config(
    session: Session,
    model: str,
    placement: str,
    *,
    keep_alive_mode: str | None = None,
    keep_alive_seconds: int | None = None,
    num_ctx_default: int | None = None,
    num_ctx_default_provided: bool = False,
) -> ModelConfig:
    cfg = session.query(ModelConfig).filter_by(model=model).first()
    if cfg is None:
        cfg = ModelConfig(model=model, placement=placement)
        session.add(cfg)
    else:
        cfg.placement = placement
    if placement in ("on_demand", "available"):
        cfg.keep_alive_mode = "unload"
    elif keep_alive_mode is not None:
        cfg.keep_alive_mode = keep_alive_mode
    if keep_alive_seconds is not None:
        cfg.keep_alive_seconds = keep_alive_seconds
        if keep_alive_mode is None:
            cfg.keep_alive_mode = "finite"
    if num_ctx_default_provided:
        cfg.num_ctx_default = num_ctx_default
    session.flush()
    return cfg


def _validate_keep_alive(mode: str | None, seconds: int | None) -> None:
    if mode is not None and mode not in {"default", "finite", "permanent", "unload"}:
        raise HTTPException(422, detail={"detail": "invalid_keep_alive_mode", "mode": mode})
    if seconds is not None and seconds < 0:
        raise HTTPException(422, detail={"detail": "invalid_keep_alive_seconds"})
    if mode == "finite" and seconds is None:
        raise HTTPException(422, detail={"detail": "finite_keep_alive_requires_seconds"})


def _upsert_metadata_from_details(
    session: Session,
    *,
    model: str,
    details,
    local_modified_at=None,
) -> ModelMetadata:
    row = session.query(ModelMetadata).filter_by(model=model).first()
    if row is None:
        row = ModelMetadata(model=model)
        session.add(row)
    row.parameter_size = details.parameter_size
    row.quantization_level = details.quantization_level
    row.architecture_context_length = details.architecture_context_length
    row.capabilities_json = json.dumps(details.capabilities or [])
    row.local_modified_at = details.modified_at or local_modified_at
    row.metadata_refreshed_at = datetime.now(UTC).replace(tzinfo=None)
    session.flush()
    return row


@router.post(
    "/models/{model}/place",
    response_model=PlaceResponse,
    summary="Set placement for a model (admin).",
)
async def place_model(
    model: str,
    body: PlaceRequest,
    request: Request,
    response: Response,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_session),
    chat_factory=Depends(get_chat_factory),
    telemetry_factory=Depends(get_telemetry_factory),
) -> PlaceResponse:
    gpu_count = _detected_gpu_count(request)
    allowed = _allowed_placements(gpu_count)
    if body.placement not in allowed:
        raise HTTPException(
            422,
            detail={
                "detail": "invalid_placement",
                "placement": body.placement,
                "allowed": allowed,
            },
        )

    _validate_keep_alive(body.keep_alive_mode, body.keep_alive_seconds)
    options = _options_for_placement(
        body.placement,
        keep_alive_mode=body.keep_alive_mode,
        keep_alive_seconds=body.keep_alive_seconds,
        num_ctx_default=body.num_ctx_default,
    )
    expected_main_gpu = _expected_main_gpu(body.placement)
    should_be_loaded = _placement_should_be_loaded(body.placement)

    # UPSERT first so observers see the desired state even if warm-up fails.
    cfg = db.query(ModelConfig).filter_by(model=model).first()
    old_placement = cfg.placement if cfg is not None else None
    cfg = _upsert_model_config(
        db,
        model,
        body.placement,
        keep_alive_mode=body.keep_alive_mode,
        keep_alive_seconds=body.keep_alive_seconds,
        num_ctx_default=body.num_ctx_default,
    )

    lock = request.app.state.model_locks[model]
    main_gpu_actual: int | None = None
    mismatch = False
    loaded_now = False

    async with lock:
        async with _AdapterScope(lambda: chat_factory(request.app.state.settings.ollama_url)) as chat:
            telemetry: Telemetry | None = None
            try:
                async with _AdapterScope(telemetry_factory) as tel:
                    telemetry = tel
                    before: list[GpuSnapshot] | None = None
                    try:
                        before = await telemetry.sample()
                    except Exception:
                        before = None

                    try:
                        await _warm_up(chat, model, options)
                    except OllamaModelNotFound:
                        raise HTTPException(404, detail="model_not_found")
                    except OllamaUnreachableError as exc:
                        raise HTTPException(503, detail={"detail": "ollama_unreachable", "cause": str(exc)})

                    if should_be_loaded:
                        loaded_now = await _wait_loaded(chat, model, timeout_s=LOADED_CONFIRMATION_TIMEOUT_S)
                    else:
                        unloaded = await _wait_unloaded(chat, model, timeout_s=LOADED_CONFIRMATION_TIMEOUT_S)
                        loaded_now = not unloaded

                    after: list[GpuSnapshot] | None = None
                    try:
                        after = await telemetry.sample()
                    except Exception:
                        after = None

                    main_gpu_actual = _detect_main_gpu_actual(before, after)
                    if expected_main_gpu is not None and main_gpu_actual is not None:
                        mismatch = main_gpu_actual != expected_main_gpu
                    try:
                        details = await chat.show_model(model)
                        _upsert_metadata_from_details(db, model=model, details=details)
                    except (OllamaModelNotFound, OllamaUnreachableError, OllamaResponseError):
                        pass
            except HTTPException:
                raise

    write_admin_audit(
        db,
        actor_id=user.id,
        action="model_place",
        target_model=model,
        details={
            "old": old_placement,
            "new": body.placement,
            "applied": options,
            "mismatch": mismatch,
            "main_gpu_actual": main_gpu_actual,
        },
        source_ip=request.client.host if request.client else None,
    )
    db.commit()

    return PlaceResponse(
        applied=PlaceApplied(
            keep_alive=options.get("keep_alive", 0),
            keep_alive_seconds=(
                int(options["keep_alive"]) if isinstance(options.get("keep_alive"), int) and options["keep_alive"] >= 0 else None
            ),
            keep_alive_mode=cfg.keep_alive_mode,
            main_gpu=options.get("main_gpu"),
            num_gpu=options.get("num_gpu"),
            num_ctx=options.get("num_ctx"),
        ),
        loaded_now=loaded_now,
        mismatch=mismatch,
        main_gpu_actual=main_gpu_actual,
    )


# --- Pull endpoint --------------------------------------------------------


@router.post(
    "/models/{model}/pull",
    summary="Pull a model from the Ollama registry (admin). Streams progress as SSE.",
)
async def pull_model(
    model: str,
    request: Request,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_session),
    chat_factory=Depends(get_chat_factory),
) -> EventSourceResponse:
    settings = request.app.state.settings
    actor_id = user.id
    source_ip = request.client.host if request.client else None
    session_factory = request.app.state.session_factory

    async def gen() -> AsyncIterator[dict]:
        succeeded = False
        last_status: str | None = None
        async with _AdapterScope(lambda: chat_factory(settings.ollama_url)) as chat:
            try:
                async for progress in chat.pull_model(model):
                    last_status = progress.status
                    yield {
                        "event": "progress",
                        "data": json.dumps(
                            {
                                "status": progress.status,
                                "digest": progress.digest,
                                "total": progress.total,
                                "completed": progress.completed,
                            }
                        ),
                    }
                    if progress.status == "success":
                        succeeded = True
            except OllamaUnreachableError as exc:
                yield {
                    "event": "error",
                    "data": json.dumps({"detail": "ollama_unreachable", "cause": str(exc)}),
                }
                return

        # On success: ensure a default model_config row exists, write audit.
        with session_factory() as session:
            if succeeded:
                existing = session.query(ModelConfig).filter_by(model=model).first()
                if existing is None:
                    session.add(ModelConfig(model=model, placement="available"))
                    session.flush()
                try:
                    details = await chat.show_model(model)
                    _upsert_metadata_from_details(session, model=model, details=details)
                except (OllamaModelNotFound, OllamaUnreachableError, OllamaResponseError):
                    pass
            write_admin_audit(
                session,
                actor_id=actor_id,
                action="model_pull",
                target_model=model,
                details={"status": last_status, "success": succeeded},
                source_ip=source_ip,
            )
            session.commit()
        yield {
            "event": "done",
            "data": json.dumps({"success": succeeded, "status": last_status}),
        }

    return EventSourceResponse(gen())


# --- Delete endpoint ------------------------------------------------------


@router.delete(
    "/models/{model}",
    summary="Delete a model from Ollama (admin).",
    status_code=204,
)
async def delete_model(
    model: str,
    request: Request,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_session),
    chat_factory=Depends(get_chat_factory),
) -> Response:
    async with _AdapterScope(lambda: chat_factory(request.app.state.settings.ollama_url)) as chat:
        try:
            await chat.delete_model(model)
        except OllamaModelNotFound:
            raise HTTPException(404, detail="model_not_found")
        except OllamaUnreachableError as exc:
            raise HTTPException(503, detail={"detail": "ollama_unreachable", "cause": str(exc)})

    db.execute(delete(ModelConfig).where(ModelConfig.model == model))
    db.execute(delete(ModelTag).where(ModelTag.model == model))
    db.execute(delete(ModelMetadata).where(ModelMetadata.model == model))
    write_admin_audit(
        db,
        actor_id=user.id,
        action="model_delete",
        target_model=model,
        details={"model": model},
        source_ip=request.client.host if request.client else None,
    )
    db.commit()
    return Response(status_code=204)


# --- Settings patch endpoint ---------------------------------------------


@router.patch(
    "/models/{model}/settings",
    summary="Patch a model's per-model settings (admin).",
)
async def patch_settings(
    model: str,
    body: ModelSettingsPatch,
    request: Request,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_session),
) -> dict:
    cfg = db.query(ModelConfig).filter_by(model=model).first()
    if cfg is None:
        cfg = ModelConfig(model=model, placement="available")
        db.add(cfg)
    changes: dict[str, Any] = {}
    if body.keep_alive_mode is not None:
        _validate_keep_alive(body.keep_alive_mode, body.keep_alive_seconds)
        cfg.keep_alive_mode = body.keep_alive_mode
        changes["keep_alive_mode"] = body.keep_alive_mode
    if body.keep_alive_seconds is not None:
        cfg.keep_alive_seconds = body.keep_alive_seconds
        if body.keep_alive_mode is None:
            cfg.keep_alive_mode = "finite"
        changes["keep_alive_seconds"] = body.keep_alive_seconds
    if "num_ctx_default" in body.model_fields_set:
        cfg.num_ctx_default = body.num_ctx_default
        changes["num_ctx_default"] = body.num_ctx_default
    if body.single_flight is not None:
        cfg.single_flight = 1 if body.single_flight else 0
        changes["single_flight"] = bool(body.single_flight)
    if body.notes is not None:
        cfg.notes = body.notes
        changes["notes"] = body.notes
    db.flush()

    write_admin_audit(
        db,
        actor_id=user.id,
        action="model_settings_patch",
        target_model=model,
        details=changes,
        source_ip=request.client.host if request.client else None,
    )
    db.commit()
    return {"updated": changes}


@router.post(
    "/models/metadata/refresh",
    summary="Refresh cached /api/show metadata for known models (admin).",
)
async def refresh_model_metadata(
    request: Request,
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_session),
    chat_factory=Depends(get_chat_factory),
) -> dict[str, Any]:
    refreshed: list[str] = []
    errors: dict[str, str] = {}
    async with _AdapterScope(lambda: chat_factory(request.app.state.settings.ollama_url)) as chat:
        try:
            models = await chat.list_models()
        except OllamaUnreachableError as exc:
            raise HTTPException(503, detail={"detail": "ollama_unreachable", "cause": str(exc)})
        for info in models:
            try:
                details = await chat.show_model(info.name)
                _upsert_metadata_from_details(
                    db,
                    model=info.name,
                    details=details,
                    local_modified_at=info.modified,
                )
                refreshed.append(info.name)
            except Exception as exc:  # best-effort metadata surface
                errors[info.name] = str(exc)
    write_admin_audit(
        db,
        actor_id=user.id,
        action="model_metadata_refresh",
        target_model=None,
        details={"refreshed": refreshed, "errors": errors},
        source_ip=request.client.host if request.client else None,
    )
    db.commit()
    return {"refreshed": refreshed, "errors": errors}


# --- Performance harness --------------------------------------------------


async def _drop_model(chat: LLMChat, model: str) -> None:
    """Issue a one-shot generate with keep_alive=0 to drop the model.

    Embedding-only models (e.g. nomic-embed-text) return HTTP 400
    "does not support chat". Treat that as a successful no-op — the model
    was never loaded via chat so there's nothing to unload.
    """
    try:
        async for _chunk in chat.chat_stream(
            model=model,
            messages=[{"role": "user", "content": " "}],
            options={"keep_alive": 0},
        ):
            break
    except (OllamaModelNotFound, OllamaResponseError):
        # Already gone, or model doesn't support chat (embedding-only).
        return


async def _measure_throughput(chat: LLMChat, model: str) -> float | None:
    """ADR-005 §4 step 2 — return tokens/second from the final chunk's usage."""
    final = None
    async for chunk in chat.chat_stream(
        model=model,
        messages=[{"role": "user", "content": "Count to ten."}],
        options={"keep_alive": PLACEMENT_KEEP_ALIVE_WARM_S},
    ):
        if chunk.done:
            final = chunk
    if (
        final is None
        or final.usage_out is None
        or final.eval_duration_ns is None
        or final.eval_duration_ns == 0
    ):
        return None
    return final.usage_out / (final.eval_duration_ns / 1e9)


async def _probe_max_context(chat: LLMChat, model: str, contexts: list[int]) -> int | None:
    """Walk the contexts list from largest to smallest. Return the first
    that succeeds.

    Assumption (documented per Chris's runbook): the spec doesn't fully
    specify the search strategy. We pick "largest-first" because users
    typically want to know the ceiling, and a 65k probe failing fast is
    cheap. A binary-search variant would also work; switching is local
    to this function.
    """
    for ctx in sorted(contexts, reverse=True):
        try:
            async for chunk in chat.chat_stream(
                model=model,
                messages=[{"role": "user", "content": "x"}],
                options={"num_ctx": ctx, "keep_alive": PLACEMENT_KEEP_ALIVE_WARM_S},
            ):
                if chunk.done:
                    return ctx
                # First non-done chunk is enough — it means the load worked.
                return ctx
        except (OllamaResponseError, OllamaUnreachableError):
            continue
        except OllamaModelNotFound:
            return None
    return None


def _gpu_layout_diff(before: list[GpuSnapshot] | None, after: list[GpuSnapshot] | None) -> dict[str, int]:
    if not before or not after:
        return {}
    before_by_idx = {s.index: s.vram_used_mb for s in before}
    return {f"gpu{s.index}_vram_growth_mb": s.vram_used_mb - before_by_idx.get(s.index, 0) for s in after}


def _save_model_perf(
    session: Session,
    *,
    model: str,
    cold_load_seconds: float | None,
    throughput_tps: float | None,
    max_ctx_observed: int | None,
    gpu_layout: dict[str, int],
    placement_tested: str | None = None,
    gpu_count_at_test: int | None = None,
    num_ctx_used: int | None = None,
    keep_alive_used: str | None = None,
) -> ModelPerf:
    row = ModelPerf(
        model=model,
        cold_load_seconds=cold_load_seconds,
        throughput_tps=throughput_tps,
        max_ctx_observed=max_ctx_observed,
        gpu_layout_json=json.dumps(gpu_layout) if gpu_layout else None,
        placement_tested=placement_tested,
        gpu_count_at_test=gpu_count_at_test,
        num_ctx_used=num_ctx_used,
        keep_alive_used=keep_alive_used,
    )
    session.add(row)
    session.flush()
    return row


def _last_perf_row(session: Session, model: str) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(ModelPerf).where(ModelPerf.model == model).order_by(ModelPerf.measured_at.desc()).limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        return None
    return {
        "id": row.id,
        "model": row.model,
        "measured_at": row.measured_at.isoformat() if row.measured_at else None,
        "cold_load_seconds": row.cold_load_seconds,
        "throughput_tps": row.throughput_tps,
        "max_ctx_observed": row.max_ctx_observed,
        "placement_tested": row.placement_tested,
        "gpu_count_at_test": row.gpu_count_at_test,
        "num_ctx_used": row.num_ctx_used,
        "keep_alive_used": row.keep_alive_used,
        "gpu_layout_diff": json.loads(row.gpu_layout_json) if row.gpu_layout_json else {},
    }


def _sse(event: str, payload: dict[str, Any]) -> dict[str, str]:
    return {"event": event, "data": json.dumps(payload, default=str)}


def _stage_payload(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "started_at": datetime.now(UTC).isoformat(),
    }


def _progress_payload(
    state: _PerfRunState,
    *,
    tokens_so_far: int | None = None,
    tokens_per_sec: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "stage": state.stage,
        "elapsed_ms": state.elapsed_ms(),
    }
    if tokens_so_far is not None:
        payload["tokens_so_far"] = tokens_so_far
    if tokens_per_sec is not None:
        payload["tokens_per_sec"] = tokens_per_sec
    return payload


async def _emit_stage(state: _PerfRunState, name: str) -> AsyncIterator[dict[str, str]]:
    state.stage = name
    state.last_event_at = time.monotonic()
    yield _sse("stage", _stage_payload(name))
    state.last_event_at = time.monotonic()
    yield _sse("progress", _progress_payload(state))


async def _await_with_heartbeat(
    awaitable,
    state: _PerfRunState,
    result: dict[str, Any] | None = None,
    result_key: str = "value",
) -> AsyncIterator[dict[str, str]]:
    task = asyncio.create_task(awaitable)
    try:
        while True:
            if state.cancel_event.is_set():
                task.cancel()
                raise _PerfCancelled
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=PERF_HEARTBEAT_INTERVAL_S)
                if task.exception() is not None:
                    raise task.exception()  # type: ignore[misc]
                if result is not None:
                    result[result_key] = task.result()
                return
            except TimeoutError:
                now = time.monotonic()
                if now - state.last_event_at >= PERF_HEARTBEAT_INTERVAL_S:
                    state.last_event_at = now
                    yield _sse("heartbeat", _progress_payload(state))
    finally:
        if not task.done():
            task.cancel()


async def _acquire_lock_with_events(
    lock: asyncio.Lock,
    state: _PerfRunState,
) -> AsyncIterator[dict[str, str]]:
    async for event in _await_with_heartbeat(lock.acquire(), state):
        yield event


async def _drop_model_with_events(
    chat: LLMChat,
    model: str,
    state: _PerfRunState,
) -> AsyncIterator[dict[str, str]]:
    async for event in _await_with_heartbeat(_drop_model(chat, model), state):
        yield event


async def _wait_unloaded_with_events(
    chat: LLMChat,
    model: str,
    state: _PerfRunState,
    *,
    timeout_s: float,
) -> AsyncIterator[dict[str, str]]:
    async for event in _await_with_heartbeat(_wait_unloaded(chat, model, timeout_s=timeout_s), state):
        yield event


async def _telemetry_sample_with_events(
    telemetry: Telemetry,
    state: _PerfRunState,
    result: dict[str, Any],
    result_key: str,
) -> AsyncIterator[dict[str, str]]:
    async for event in _await_with_heartbeat(telemetry.sample(), state, result, result_key):
        yield event


async def _restore_prior_placement(
    chat: LLMChat,
    model: str,
    prior_placement: str | None,
    state: _PerfRunState,
) -> AsyncIterator[dict[str, str]]:
    if prior_placement is not None and _placement_should_be_loaded(prior_placement):
        async for event in _await_with_heartbeat(
            _warm_up(chat, model, _options_for_placement(prior_placement)), state
        ):
            yield event
    elif prior_placement is not None and not _placement_should_be_loaded(prior_placement):
        async for event in _await_with_heartbeat(_drop_model(chat, model), state):
            yield event


async def _cold_load_with_events(
    chat: LLMChat,
    model: str,
    state: _PerfRunState,
    result: dict[str, Any],
    result_key: str,
) -> AsyncIterator[dict[str, str]]:
    first_byte_t: float | None = None
    stream = chat.chat_stream(
        model=model,
        messages=[{"role": "user", "content": "Reply with: ok"}],
        options={"keep_alive": PLACEMENT_KEEP_ALIVE_WARM_S},
    )
    agen = stream.__aiter__()
    next_task: asyncio.Task | None = None
    while first_byte_t is None:
        if state.cancel_event.is_set():
            aclose = getattr(agen, "aclose", None)
            if aclose is not None:
                await aclose()
            raise _PerfCancelled
        if next_task is None:
            next_task = asyncio.create_task(agen.__anext__())
        try:
            try:
                await asyncio.wait_for(asyncio.shield(next_task), timeout=PERF_HEARTBEAT_INTERVAL_S)
                first_byte_t = time.monotonic()
            except TimeoutError:
                now = time.monotonic()
                if now - state.last_event_at >= PERF_HEARTBEAT_INTERVAL_S:
                    state.last_event_at = now
                    yield _sse("heartbeat", _progress_payload(state))
        except StopAsyncIteration:
            break
        finally:
            if next_task is not None and not next_task.done() and state.cancel_event.is_set():
                next_task.cancel()
        if next_task is not None and next_task.done():
            next_task = None
    result[result_key] = first_byte_t


async def _measure_throughput_with_events(
    chat: LLMChat,
    model: str,
    state: _PerfRunState,
    result: dict[str, Any],
    result_key: str,
) -> AsyncIterator[dict[str, str]]:
    final = None
    tokens_so_far = 0
    run_started_at = time.monotonic()
    stream = chat.chat_stream(
        model=model,
        messages=[{"role": "user", "content": "Count to ten."}],
        options={"keep_alive": PLACEMENT_KEEP_ALIVE_WARM_S},
    )
    agen = stream.__aiter__()
    next_task: asyncio.Task | None = None
    while True:
        if state.cancel_event.is_set():
            aclose = getattr(agen, "aclose", None)
            if aclose is not None:
                await aclose()
            raise _PerfCancelled
        if next_task is None:
            next_task = asyncio.create_task(agen.__anext__())
        try:
            try:
                chunk = await asyncio.wait_for(asyncio.shield(next_task), timeout=PERF_HEARTBEAT_INTERVAL_S)
            except TimeoutError:
                now = time.monotonic()
                if now - state.last_event_at >= PERF_HEARTBEAT_INTERVAL_S:
                    state.last_event_at = now
                    yield _sse(
                        "heartbeat",
                        _progress_payload(
                            state,
                            tokens_so_far=tokens_so_far,
                            tokens_per_sec=(tokens_so_far / max(now - run_started_at, 0.001)),
                        ),
                    )
                continue
        except StopAsyncIteration:
            break
        finally:
            if next_task is not None and not next_task.done() and state.cancel_event.is_set():
                next_task.cancel()
        if next_task is not None and next_task.done():
            next_task = None
        if chunk.done:
            final = chunk
            if chunk.usage_out is not None:
                tokens_so_far += chunk.usage_out
        else:
            tokens_so_far += 1
        now = time.monotonic()
        if now - state.last_event_at >= PERF_HEARTBEAT_INTERVAL_S or chunk.done:
            state.last_event_at = now
            yield _sse(
                "progress",
                _progress_payload(
                    state,
                    tokens_so_far=tokens_so_far,
                    tokens_per_sec=tokens_so_far / max(now - run_started_at, 0.001),
                ),
            )
        if chunk.done:
            break
    if (
        final is None
        or final.usage_out is None
        or final.eval_duration_ns is None
        or final.eval_duration_ns == 0
    ):
        result[result_key] = None
    else:
        result[result_key] = final.usage_out / (final.eval_duration_ns / 1e9)


async def _probe_max_context_with_events(
    chat: LLMChat,
    model: str,
    contexts: list[int],
    state: _PerfRunState,
    result: dict[str, Any],
    result_key: str,
) -> AsyncIterator[dict[str, str]]:
    async for event in _await_with_heartbeat(
        _probe_max_context(chat, model, contexts), state, result, result_key
    ):
        yield event


@router.post(
    "/models/{model}/perf-test",
    summary="Run the cold-load + throughput + max-ctx perf harness (admin). SSE.",
)
async def perf_test(
    model: str,
    request: Request,
    body: PerfTestRequest | None = None,
    user: User = Depends(require_role("admin")),
    chat_factory=Depends(get_chat_factory),
    telemetry_factory=Depends(get_telemetry_factory),
) -> EventSourceResponse:
    """Per ADR-005 §4 + UC-02 §perf harness.

    Single-flight at the host level via `app.state.host_perf_lock`; per-model
    via `app.state.model_locks[model]`. Restores the prior `model_config.placement`
    on completion so the user's expected state is preserved.
    """
    contexts = (body.contexts if body else None) or DEFAULT_CONTEXTS
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    model_locks = request.app.state.model_locks
    host_perf_lock = request.app.state.host_perf_lock
    active_runs: dict[str, _PerfRunState] = request.app.state.perf_test_runs
    actor_id = user.id
    source_ip = request.client.host if request.client else None
    if model in active_runs:
        raise HTTPException(409, detail="perf_test_already_running")
    run_state = _PerfRunState(model=model)
    active_runs[model] = run_state

    async def gen() -> AsyncIterator[dict]:
        # Record the prior placement so we can restore at the end.
        with session_factory() as s:
            cfg = s.query(ModelConfig).filter_by(model=model).first()
            prior_placement = cfg.placement if cfg is not None else None
            prior_keep_alive_mode = cfg.keep_alive_mode if cfg is not None else "default"
            prior_keep_alive_seconds = cfg.keep_alive_seconds if cfg is not None else None
            prior_num_ctx = cfg.num_ctx_default if cfg is not None else None

        try:
            host_lock_acquired = False
            model_lock_acquired = False
            try:
                async for event in _emit_stage(run_state, "lock"):
                    yield event
                async for event in _acquire_lock_with_events(host_perf_lock, run_state):
                    yield event
                host_lock_acquired = True
                async for event in _acquire_lock_with_events(model_locks[model], run_state):
                    yield event
                model_lock_acquired = True
                async with _AdapterScope(lambda: chat_factory(settings.ollama_url)) as chat:
                    async with _AdapterScope(telemetry_factory) as telemetry:
                        # Stage: unload (best effort).
                        async for event in _emit_stage(run_state, "unload"):
                            yield event
                        async for event in _drop_model_with_events(chat, model, run_state):
                            yield event
                        async for event in _wait_unloaded_with_events(chat, model, run_state, timeout_s=15.0):
                            yield event

                        # Stage: cold_load.
                        async for event in _emit_stage(run_state, "cold_load"):
                            yield event
                        samples: dict[str, Any] = {"before": None, "after": None}
                        try:
                            async for event in _telemetry_sample_with_events(
                                telemetry, run_state, samples, "before"
                            ):
                                yield event
                        except Exception:
                            samples["before"] = None
                        t0 = time.monotonic()
                        cold_result: dict[str, Any] = {"first_byte": None}
                        async for event in _cold_load_with_events(
                            chat, model, run_state, cold_result, "first_byte"
                        ):
                            yield event
                        first_byte_t = cold_result["first_byte"]
                        cold_load_seconds = (first_byte_t - t0) if first_byte_t is not None else None
                        try:
                            async for event in _telemetry_sample_with_events(
                                telemetry, run_state, samples, "after"
                            ):
                                yield event
                        except Exception:
                            samples["after"] = None
                        gpu_layout = _gpu_layout_diff(samples["before"], samples["after"])

                        # Stage: throughput.
                        async for event in _emit_stage(run_state, "throughput"):
                            yield event
                        tps_runs: list[float] = []
                        for _ in range(THROUGHPUT_RUNS):
                            tps_result: dict[str, Any] = {"tps": None}
                            async for event in _measure_throughput_with_events(
                                chat, model, run_state, tps_result, "tps"
                            ):
                                yield event
                            if tps_result["tps"] is not None:
                                tps_runs.append(tps_result["tps"])
                        mean_tps = statistics.mean(tps_runs) if tps_runs else None

                        # Stage: context probe.
                        async for event in _emit_stage(run_state, "context_probe"):
                            yield event
                        ctx_result: dict[str, Any] = {"max_ctx": None}
                        async for event in _probe_max_context_with_events(
                            chat, model, contexts, run_state, ctx_result, "max_ctx"
                        ):
                            yield event
                        max_ctx = ctx_result["max_ctx"]

                        # Stage: persist.
                        async for event in _emit_stage(run_state, "persist"):
                            yield event
                        with session_factory() as s:
                            row = _save_model_perf(
                                s,
                                model=model,
                                cold_load_seconds=cold_load_seconds,
                                throughput_tps=mean_tps,
                                max_ctx_observed=max_ctx,
                                gpu_layout=gpu_layout,
                                placement_tested=prior_placement,
                                gpu_count_at_test=len(request.app.state.gpu_state.last_snapshots or []),
                                num_ctx_used=prior_num_ctx,
                                keep_alive_used=(
                                    str(_resolve_keep_alive(
                                        prior_placement or "on_demand",
                                        keep_alive_mode=prior_keep_alive_mode,
                                        keep_alive_seconds=prior_keep_alive_seconds,
                                    ))
                                ),
                            )
                            write_admin_audit(
                                s,
                                actor_id=actor_id,
                                action="model_perf_test",
                                target_model=model,
                                details={
                                    "row_id": row.id,
                                    "cold_load_seconds": cold_load_seconds,
                                    "throughput_tps": mean_tps,
                                    "max_ctx_observed": max_ctx,
                                    "placement_tested": prior_placement,
                                },
                                source_ip=source_ip,
                            )
                            s.commit()

                        # Stage: restore before terminal result.
                        async for event in _emit_stage(run_state, "restore"):
                            yield event
                        async for event in _restore_prior_placement(chat, model, prior_placement, run_state):
                            yield event
                        with session_factory() as s:
                            result = _last_perf_row(s, model)
                        yield _sse("result", result or {})
            finally:
                if model_lock_acquired:
                    model_locks[model].release()
                if host_lock_acquired:
                    host_perf_lock.release()
        except _PerfCancelled:
            async with _AdapterScope(lambda: chat_factory(settings.ollama_url)) as chat:
                try:
                    async for event in _restore_prior_placement(chat, model, prior_placement, run_state):
                        yield event
                except Exception:
                    log.exception("Perf-test restore after cancel failed for %s", model)
            with session_factory() as s:
                write_admin_audit(
                    s,
                    actor_id=actor_id,
                    action="model_perf_test_cancel",
                    target_model=model,
                    details={
                        "stage_at_cancel": run_state.stage,
                        "elapsed_ms": run_state.elapsed_ms(),
                    },
                    source_ip=source_ip,
                )
                s.commit()
            yield _sse(
                "cancelled",
                {
                    "stage_at_cancel": run_state.stage,
                    "elapsed_ms": run_state.elapsed_ms(),
                },
            )
        except OllamaModelNotFound:
            yield _sse("error", {"stage": run_state.stage, "message": "model_not_found"})
        except OllamaUnreachableError as exc:
            yield _sse(
                "error",
                {"stage": run_state.stage, "message": f"ollama_unreachable: {exc}"},
            )
        except OllamaResponseError as exc:
            yield _sse(
                "error",
                {"stage": run_state.stage, "message": f"model_not_supported: {exc}"},
            )
        except Exception as exc:
            log.exception("Perf test failed for %s", model)
            yield _sse("error", {"stage": run_state.stage, "message": str(exc)})
        finally:
            active_runs.pop(model, None)

    return EventSourceResponse(gen())


@router.post(
    "/models/{model}/perf-test/cancel",
    summary="Cancel an active model performance test (admin).",
)
async def cancel_perf_test(
    model: str,
    request: Request,
    user: User = Depends(require_role("admin")),
) -> dict[str, bool]:
    run_state: _PerfRunState | None = request.app.state.perf_test_runs.get(model)
    if run_state is None:
        return {"cancelled": False}
    run_state.cancel_event.set()
    return {"cancelled": True}


# =========================================================================
# UC-10 — Model tag management
# =========================================================================


_VALID_TAGS = {"chat", "code", "both"}


def _client_ip(request: Request) -> str | None:
    if request.client is not None:
        return request.client.host
    return None


def _available_model_names(request: Request) -> list[str]:
    """Read the cached model list from `app.state.model_state`. Used by
    the heuristic re-evaluation flow so we don't hit Ollama on every
    settings save / tag clear."""
    state = request.app.state.model_state
    return [m.name for m in (state.available_models or [])]


@router.patch(
    "/models/{model}/tag",
    response_model=ModelTagResponse,
    summary="Override a model's chat/code/both tag (admin).",
)
def patch_model_tag(
    model: str,
    body: ModelTagPatchRequest,
    request: Request,
    actor: User = Depends(require_role("admin")),
    db: Session = Depends(get_session),
) -> ModelTagResponse:
    if body.tag not in _VALID_TAGS:
        raise HTTPException(
            422,
            detail={
                "detail": "invalid_tag",
                "allowed": sorted(_VALID_TAGS),
            },
        )
    existing = db.execute(select(ModelTag).where(ModelTag.model == model)).scalar_one_or_none()
    if existing is None:
        db.add(ModelTag(model=model, tag=body.tag, source="override"))
    else:
        existing.tag = body.tag
        existing.source = "override"
    db.flush()
    write_admin_audit(
        db,
        actor_id=actor.id,
        action="model_tag_set",
        target_model=model,
        details={"tag": body.tag},
        source_ip=_client_ip(request),
    )
    db.commit()
    return ModelTagResponse(model=model, tag=body.tag, source="override")


@router.delete(
    "/models/{model}/tag",
    status_code=204,
    summary="Clear a model tag override; revert to heuristic (admin).",
)
def delete_model_tag(
    model: str,
    request: Request,
    actor: User = Depends(require_role("admin")),
    db: Session = Depends(get_session),
) -> Response:
    """Remove the override row. If the model is in the cached available
    list, immediately re-apply the heuristic so the row gets back an
    `auto` tag — otherwise leave it absent (the next `ModelStateSampler`
    tick will re-insert it)."""
    existing = db.execute(select(ModelTag).where(ModelTag.model == model)).scalar_one_or_none()
    if existing is None or existing.source != "override":
        # Idempotent — no-op + no audit row when there's nothing to clear.
        return Response(status_code=204)

    db.execute(delete(ModelTag).where(ModelTag.model == model))
    db.flush()

    # Reapply the heuristic for the cached model list. If `model` is in
    # the list it'll be re-inserted with source='auto'.
    available = _available_model_names(request)
    if model not in available:
        available = sorted(set(available + [model]))
    reapply_heuristics(db, available)

    write_admin_audit(
        db,
        actor_id=actor.id,
        action="model_tag_cleared",
        target_model=model,
        details={"prior_tag": existing.tag},
        source_ip=_client_ip(request),
    )
    db.commit()
    return Response(status_code=204)


# =========================================================================
# UC-10 — Settings GET / PUT
# =========================================================================


_SETTINGS_KEYS = {"code_default_system_prompt", SETTINGS_KEY_TAG_HEURISTICS}


def _get_setting(db: Session, key: str) -> str | None:
    row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    return row.value if row is not None else None


def _put_setting(db: Session, key: str, value: str) -> None:
    row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


@router.get(
    "/settings",
    response_model=SettingsResponse,
    summary="Read the cockpit-wide admin settings (admin).",
)
def get_settings_endpoint(
    actor: User = Depends(require_role("admin")),
    db: Session = Depends(get_session),
) -> SettingsResponse:
    return SettingsResponse(
        code_default_system_prompt=_get_setting(db, "code_default_system_prompt"),
        tag_heuristics_yaml=_get_setting(db, SETTINGS_KEY_TAG_HEURISTICS),
    )


@router.put(
    "/settings",
    response_model=SettingsPutResponse,
    summary="Update one or more cockpit-wide admin settings (admin).",
)
def put_settings_endpoint(
    body: SettingsPutRequest,
    request: Request,
    actor: User = Depends(require_role("admin")),
    db: Session = Depends(get_session),
) -> SettingsPutResponse:
    """Partial-PUT: only keys present in the body are written. If
    `tag_heuristics_yaml` is supplied, it is parsed before the write
    so a malformed YAML returns 400 without touching the DB; on
    success the heuristic is re-applied across the cached model list
    so the new patterns take effect immediately."""
    updated: list[str] = []

    if body.tag_heuristics_yaml is not None:
        try:
            load_heuristic_from_yaml(body.tag_heuristics_yaml)
        except yaml.YAMLError as exc:
            raise HTTPException(
                400,
                detail={"detail": "invalid_yaml", "message": str(exc)},
            ) from exc
        _put_setting(db, SETTINGS_KEY_TAG_HEURISTICS, body.tag_heuristics_yaml)
        updated.append(SETTINGS_KEY_TAG_HEURISTICS)

    if body.code_default_system_prompt is not None:
        _put_setting(db, "code_default_system_prompt", body.code_default_system_prompt)
        updated.append("code_default_system_prompt")

    if not updated:
        # No-op; spec says "for each provided key", so the empty case is
        # legal — return without writing an audit row.
        return SettingsPutResponse(updated=[])

    if SETTINGS_KEY_TAG_HEURISTICS in updated and body.tag_heuristics_yaml is not None:
        # Re-apply heuristic over the cached model list so any auto rows
        # whose tag changed get refreshed in this same transaction.
        reapply_heuristics(
            db,
            _available_model_names(request),
            yaml_override=body.tag_heuristics_yaml,
        )

    write_admin_audit(
        db,
        actor_id=actor.id,
        action="settings_updated",
        target_model=None,
        details={"keys_changed": updated},
        source_ip=_client_ip(request),
    )
    db.commit()
    return SettingsPutResponse(updated=updated)


# =========================================================================
# UC-10 — Per-model metrics rollup + drill-down
# =========================================================================


@router.get(
    "/metrics",
    response_model=list[ModelMetricsSummary],
    summary="Per-model 7-day rollup (admin).",
)
def get_model_metrics(
    actor: User = Depends(require_role("admin")),
    db: Session = Depends(get_session),
) -> list[ModelMetricsSummary]:
    """One row per model. Counts only assistant messages with non-null
    `model`. p95 latency is *not* in this rollup — pulling all rows just
    to compute one percentile per model would be too expensive for the
    list view; the drill-down endpoint provides p95.
    """
    from sqlalchemy import func as sqlfunc

    rows = db.execute(
        select(
            Message.model,
            sqlfunc.count(Message.id).label("calls"),
            sqlfunc.coalesce(sqlfunc.sum(Message.usage_in), 0).label("prompt_tokens"),
            sqlfunc.coalesce(sqlfunc.sum(Message.usage_out), 0).label("completion_tokens"),
            sqlfunc.avg(Message.latency_ms).label("mean_latency_ms"),
            sqlfunc.avg(Message.gen_tps).label("mean_gen_tps"),
            sqlfunc.max(Message.ts).label("last_call_at"),
        )
        .where(
            Message.role == "assistant",
            Message.model.is_not(None),
            Message.ts >= sqlfunc.datetime("now", "-7 days"),
        )
        .group_by(Message.model)
        .order_by(sqlfunc.count(Message.id).desc())
    ).all()
    return [
        ModelMetricsSummary(
            model=r.model,
            calls=int(r.calls),
            prompt_tokens=int(r.prompt_tokens or 0),
            completion_tokens=int(r.completion_tokens or 0),
            mean_latency_ms=float(r.mean_latency_ms) if r.mean_latency_ms is not None else None,
            mean_gen_tps=float(r.mean_gen_tps) if r.mean_gen_tps is not None else None,
            last_call_at=r.last_call_at,
        )
        for r in rows
    ]


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    rank = 0.95 * (n - 1)
    lo = int(rank)
    frac = rank - lo
    if lo + 1 >= n:
        return sorted_vals[-1]
    return sorted_vals[lo] + frac * (sorted_vals[lo + 1] - sorted_vals[lo])


@router.get(
    "/metrics/{model}",
    response_model=ModelMetricsDrilldown,
    summary="Last 50 assistant calls + p95 latency for a model (admin).",
)
def get_model_metrics_drilldown(
    model: str,
    actor: User = Depends(require_role("admin")),
    db: Session = Depends(get_session),
) -> ModelMetricsDrilldown:
    rows = db.execute(
        select(
            Message.role,
            Message.usage_in,
            Message.usage_out,
            Message.latency_ms,
            Message.gen_tps,
            Message.ts,
            Message.error,
        )
        .where(Message.model == model, Message.role == "assistant")
        .order_by(Message.ts.desc())
        .limit(50)
    ).all()

    calls = [
        ModelCallEntry(
            role=r.role,
            usage_in=r.usage_in,
            usage_out=r.usage_out,
            latency_ms=r.latency_ms,
            gen_tps=r.gen_tps,
            ts=r.ts,
            error=r.error,
        )
        for r in rows
    ]
    latencies = [float(r.latency_ms) for r in rows if r.latency_ms is not None]
    return ModelMetricsDrilldown(calls=calls, p95_latency_ms=_p95(latencies))
