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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from cockpit.deps import get_chat_factory, get_session, get_settings, get_telemetry_factory
from cockpit.models import ModelConfig, ModelPerf, ModelTag, User
from cockpit.ports.llm_chat import (
    LLMChat,
    OllamaModelNotFound,
    OllamaResponseError,
    OllamaUnreachableError,
)
from cockpit.ports.telemetry import GpuSnapshot, Telemetry
from cockpit.routers.auth import require_role
from cockpit.schemas import (
    ModelSettingsPatch,
    PerfTestRequest,
    PlaceApplied,
    PlaceRequest,
    PlaceResponse,
)
from cockpit.services.audit import write_admin_audit

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


def _options_for_placement(placement: str) -> dict[str, Any]:
    """UC-02 §placement transition table.

    | placement      | keep_alive | main_gpu | num_gpu |
    |----------------|------------|----------|---------|
    | gpu0..gpuN     | 24h        | int      | omitted |
    | multi_gpu      | 24h        | omitted  | 99      |
    | on_demand      | 0          | omitted  | omitted |
    | available      | 0          | omitted  | omitted |
    """
    if placement.startswith("gpu") and placement != "multi_gpu":
        gpu_idx = int(placement[3:])
        return {"keep_alive": PLACEMENT_KEEP_ALIVE_WARM_S, "main_gpu": gpu_idx}
    if placement == "multi_gpu":
        return {"keep_alive": PLACEMENT_KEEP_ALIVE_WARM_S, "num_gpu": 99}
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


async def _warm_up(
    chat: LLMChat, model: str, options: dict[str, Any]
) -> None:
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


def _detect_main_gpu_actual(
    before: list[GpuSnapshot] | None, after: list[GpuSnapshot] | None
) -> int | None:
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


def _upsert_model_config(session: Session, model: str, placement: str) -> ModelConfig:
    cfg = session.query(ModelConfig).filter_by(model=model).first()
    if cfg is None:
        cfg = ModelConfig(model=model, placement=placement)
        session.add(cfg)
    else:
        cfg.placement = placement
    session.flush()
    return cfg


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

    options = _options_for_placement(body.placement)
    expected_main_gpu = _expected_main_gpu(body.placement)
    should_be_loaded = _placement_should_be_loaded(body.placement)

    # UPSERT first so observers see the desired state even if warm-up fails.
    cfg = db.query(ModelConfig).filter_by(model=model).first()
    old_placement = cfg.placement if cfg is not None else None
    _upsert_model_config(db, model, body.placement)

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
                        loaded_now = await _wait_loaded(
                            chat, model, timeout_s=LOADED_CONFIRMATION_TIMEOUT_S
                        )
                    else:
                        unloaded = await _wait_unloaded(
                            chat, model, timeout_s=LOADED_CONFIRMATION_TIMEOUT_S
                        )
                        loaded_now = not unloaded

                    after: list[GpuSnapshot] | None = None
                    try:
                        after = await telemetry.sample()
                    except Exception:
                        after = None

                    main_gpu_actual = _detect_main_gpu_actual(before, after)
                    if expected_main_gpu is not None and main_gpu_actual is not None:
                        mismatch = main_gpu_actual != expected_main_gpu
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
            keep_alive_seconds=int(options.get("keep_alive", 0)),
            main_gpu=options.get("main_gpu"),
            num_gpu=options.get("num_gpu"),
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
    if body.keep_alive_seconds is not None:
        cfg.keep_alive_seconds = body.keep_alive_seconds
        changes["keep_alive_seconds"] = body.keep_alive_seconds
    if body.num_ctx_default is not None:
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


# --- Performance harness --------------------------------------------------


async def _drop_model(chat: LLMChat, model: str) -> None:
    """Issue a one-shot generate with keep_alive=0 to drop the model."""
    try:
        async for _chunk in chat.chat_stream(
            model=model,
            messages=[{"role": "user", "content": " "}],
            options={"keep_alive": 0},
        ):
            break
    except OllamaModelNotFound:
        # Already gone.
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
    if final is None or final.usage_out is None or final.eval_duration_ns is None or final.eval_duration_ns == 0:
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


def _gpu_layout_diff(
    before: list[GpuSnapshot] | None, after: list[GpuSnapshot] | None
) -> dict[str, int]:
    if not before or not after:
        return {}
    before_by_idx = {s.index: s.vram_used_mb for s in before}
    return {
        f"gpu{s.index}_vram_growth_mb": s.vram_used_mb - before_by_idx.get(s.index, 0)
        for s in after
    }


def _save_model_perf(
    session: Session,
    *,
    model: str,
    cold_load_seconds: float | None,
    throughput_tps: float | None,
    max_ctx_observed: int | None,
    gpu_layout: dict[str, int],
) -> ModelPerf:
    row = ModelPerf(
        model=model,
        cold_load_seconds=cold_load_seconds,
        throughput_tps=throughput_tps,
        max_ctx_observed=max_ctx_observed,
        gpu_layout_json=json.dumps(gpu_layout) if gpu_layout else None,
    )
    session.add(row)
    session.flush()
    return row


def _last_perf_row(session: Session, model: str) -> dict[str, Any] | None:
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
        "id": row.id,
        "model": row.model,
        "measured_at": row.measured_at.isoformat() if row.measured_at else None,
        "cold_load_seconds": row.cold_load_seconds,
        "throughput_tps": row.throughput_tps,
        "max_ctx_observed": row.max_ctx_observed,
    }


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
    actor_id = user.id
    source_ip = request.client.host if request.client else None

    async def gen() -> AsyncIterator[dict]:
        # Record the prior placement so we can restore at the end.
        with session_factory() as s:
            cfg = s.query(ModelConfig).filter_by(model=model).first()
            prior_placement = cfg.placement if cfg is not None else None

        async with host_perf_lock:
            yield {"event": "stage", "data": json.dumps({"name": "lock"})}
            async with model_locks[model]:
                async with _AdapterScope(lambda: chat_factory(settings.ollama_url)) as chat:
                    async with _AdapterScope(telemetry_factory) as telemetry:
                        # Stage: unload (best effort).
                        yield {"event": "stage", "data": json.dumps({"name": "unload"})}
                        await _drop_model(chat, model)
                        await _wait_unloaded(chat, model, timeout_s=15.0)

                        # Stage: cold_load.
                        yield {"event": "stage", "data": json.dumps({"name": "cold_load"})}
                        try:
                            before = await telemetry.sample()
                        except Exception:
                            before = None
                        t0 = time.monotonic()
                        first_byte_t: float | None = None
                        try:
                            async for _chunk in chat.chat_stream(
                                model=model,
                                messages=[{"role": "user", "content": "Reply with: ok"}],
                                options={"keep_alive": PLACEMENT_KEEP_ALIVE_WARM_S},
                            ):
                                first_byte_t = time.monotonic()
                                break
                        except OllamaModelNotFound:
                            yield {
                                "event": "error",
                                "data": json.dumps({"detail": "model_not_found"}),
                            }
                            return
                        except OllamaUnreachableError as exc:
                            yield {
                                "event": "error",
                                "data": json.dumps(
                                    {"detail": "ollama_unreachable", "cause": str(exc)}
                                ),
                            }
                            return
                        cold_load_seconds = (
                            (first_byte_t - t0) if first_byte_t is not None else None
                        )
                        try:
                            after = await telemetry.sample()
                        except Exception:
                            after = None
                        gpu_layout = _gpu_layout_diff(before, after)

                        # Stage: throughput.
                        yield {"event": "stage", "data": json.dumps({"name": "throughput"})}
                        tps_runs: list[float] = []
                        for _ in range(THROUGHPUT_RUNS):
                            tps = await _measure_throughput(chat, model)
                            if tps is not None:
                                tps_runs.append(tps)
                        mean_tps = statistics.mean(tps_runs) if tps_runs else None

                        # Stage: context probe.
                        yield {
                            "event": "stage",
                            "data": json.dumps({"name": "context_probe"}),
                        }
                        max_ctx = await _probe_max_context(chat, model, contexts)

                        # Stage: persist.
                        yield {"event": "stage", "data": json.dumps({"name": "persist"})}
                        with session_factory() as s:
                            row = _save_model_perf(
                                s,
                                model=model,
                                cold_load_seconds=cold_load_seconds,
                                throughput_tps=mean_tps,
                                max_ctx_observed=max_ctx,
                                gpu_layout=gpu_layout,
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
                                },
                                source_ip=source_ip,
                            )
                            s.commit()
                            result = _last_perf_row(s, model)
                        yield {"event": "result", "data": json.dumps(result, default=str)}

                        # Stage: restore.
                        yield {"event": "stage", "data": json.dumps({"name": "restore"})}
                        if prior_placement is not None and _placement_should_be_loaded(
                            prior_placement
                        ):
                            await _warm_up(
                                chat, model, _options_for_placement(prior_placement)
                            )
                        elif prior_placement is not None and not _placement_should_be_loaded(
                            prior_placement
                        ):
                            await _drop_model(chat, model)
                        # else: no prior config — leave loaded as is (the cold_load
                        # warm is at keep_alive=24h, so it stays warm by default).

    return EventSourceResponse(gen())


