"""FastAPI app factory + lifespan.

Per UC-08 functional spec §serve flow:
1. Load config.toml (the CLI does this and passes us a `Settings`).
2. Ensure DB schema is current on startup (auto `upgrade head`).
3. Probe Ollama once via `LLMChat.list_models()`; log a warning if unreachable
   but **do not** exit. The dashboard's "Ollama unreachable" badge tells the
   operator (UC-02 — Sprint 3, this commit and beyond).
4. Mount `frontend_dist/` as `StaticFiles(html=True)` for any path that
   isn't under `/api/`.

UC-02 (Sprint 3) adds:
- `telemetry_factory` DI seam (mirrors `chat_factory`).
- `app.state.gpu_state` + `app.state.model_state` populated by two
  background samplers started in the lifespan.
- `app.state.model_locks` (per-model `asyncio.Lock`) and `app.state.host_perf_lock`
  for the placement / perf-test single-flight contracts (ADR-005 §5).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from cockpit.config import Settings
from cockpit.db import make_engine, make_session_factory, upgrade_to_head
from cockpit.deps import get_session, get_settings
from cockpit.ports.llm_chat import LLMChat, OllamaResponseError, OllamaUnreachableError
from cockpit.ports.telemetry import Telemetry
from cockpit.routers import admin_ollama as admin_ollama_router
from cockpit.routers import admin_users as admin_users_router
from cockpit.routers import auth as auth_router
from cockpit.routers import chat as chat_router
from cockpit.routers import code as code_router
from cockpit.routers import code_files as code_files_router
from cockpit.routers import dashboard as dashboard_router
from cockpit.routers import dashboard_history as dashboard_history_router
from cockpit.services.aggregator import HourAggregator, MinuteAggregator
from cockpit.services.metrics import (
    GpuSampler,
    GpuSamplerState,
    ModelStateSampler,
    ModelStateSamplerState,
)

log = logging.getLogger(__name__)

ChatFactory = Callable[[str], LLMChat]
TelemetryFactory = Callable[[], Telemetry]
FRONTEND_DIST_DIR = Path(__file__).parent / "frontend_dist"


def _default_chat_factory(url: str) -> LLMChat:
    """Production factory — built lazily so importing main.py doesn't drag
    `httpx` into routers that don't use the port directly.
    """
    from cockpit.adapters.ollama_chat import OllamaLLMChat

    return OllamaLLMChat(url)


def _default_telemetry_factory() -> Telemetry:
    """Production factory for telemetry. Lazy import keeps the subprocess
    primitives out of the module-load graph for tests that don't need them.
    """
    from cockpit.adapters.telemetry import NvidiaSmiTelemetry

    return NvidiaSmiTelemetry()


def create_app(
    settings: Settings | None = None,
    *,
    chat_factory: ChatFactory | None = None,
    telemetry_factory: TelemetryFactory | None = None,
    skip_db_upgrade: bool = False,
    skip_startup_probe: bool = False,
    skip_samplers: bool = False,
) -> FastAPI:
    """Build a FastAPI app for `cockpit-admin serve` and tests.

    DI seams:
        chat_factory       — returns an LLMChat-conforming object given the URL.
                             Default builds OllamaLLMChat; tests inject FakeLLMChat.
        telemetry_factory  — returns a Telemetry-conforming object (no args).
                             Default builds NvidiaSmiTelemetry; tests inject FakeTelemetry.

    Skip flags (test fast-paths):
        skip_db_upgrade      — caller has already migrated the DB.
        skip_startup_probe   — bypass the one-shot Ollama probe at boot.
        skip_samplers        — don't start the GpuSampler / ModelStateSampler tasks.
    """
    if settings is None:
        settings = Settings()
    chat_fac = chat_factory or _default_chat_factory
    tel_fac = telemetry_factory or _default_telemetry_factory

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not skip_db_upgrade:
            try:
                upgrade_to_head(settings.db_url)
            except Exception as exc:  # pragma: no cover — pathological
                log.warning("DB upgrade on startup failed: %s", exc)
        if not skip_startup_probe:
            chat = chat_fac(settings.ollama_url)
            try:
                models = await chat.list_models()
                log.info(
                    "Ollama reachable at %s (%d models)",
                    settings.ollama_url,
                    len(models),
                )
            except OllamaUnreachableError as exc:
                log.warning(
                    "Ollama unreachable at %s — dashboard will show 'Ollama unreachable' badge. (%s)",
                    settings.ollama_url,
                    exc,
                )
            except OllamaResponseError as exc:
                log.warning("Ollama returned an error on startup probe: %s", exc)
            finally:
                aclose = getattr(chat, "aclose", None)
                if aclose is not None:
                    await aclose()

        # UC-02: long-lived sampler chat + telemetry adapters live for the
        # whole app lifetime, distinct from the boot-probe adapter above.
        sampler_chat: LLMChat | None = None
        sampler_tel: Telemetry | None = None
        sampler_tasks: list[asyncio.Task] = []
        if not skip_samplers:
            sampler_chat = chat_fac(settings.ollama_url)
            sampler_tel = tel_fac()
            gpu_sampler = GpuSampler(
                telemetry=sampler_tel,
                session_factory=app.state.session_factory,
                state=app.state.gpu_state,
            )
            model_sampler = ModelStateSampler(
                chat=sampler_chat,
                state=app.state.model_state,
            )
            app.state.gpu_sampler = gpu_sampler
            app.state.model_sampler = model_sampler
            # UC-03 — down-sample aggregators that feed the dashboard
            # history charts. The minute aggregator runs every 60 s
            # (intentionally faster than the spec's hourly batch — see
            # services/aggregator.py module docstring for the rationale).
            minute_aggregator = MinuteAggregator(
                session_factory=app.state.session_factory,
            )
            hour_aggregator = HourAggregator(
                session_factory=app.state.session_factory,
            )
            app.state.minute_aggregator = minute_aggregator
            app.state.hour_aggregator = hour_aggregator
            sampler_tasks = [
                asyncio.create_task(gpu_sampler.run(), name="cockpit-gpu-sampler"),
                asyncio.create_task(model_sampler.run(), name="cockpit-model-sampler"),
                asyncio.create_task(
                    minute_aggregator.run(), name="cockpit-minute-aggregator"
                ),
                asyncio.create_task(
                    hour_aggregator.run(), name="cockpit-hour-aggregator"
                ),
            ]
            app.state.sampler_tasks = sampler_tasks
            # Also kick a single sample so /api/dashboard/snapshot has data
            # immediately on the first request rather than waiting up to 5s/30s.
            await gpu_sampler.sample_once()
            await model_sampler.sample_once()

        try:
            yield
        finally:
            for task in sampler_tasks:
                task.cancel()
            for task in sampler_tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            for adapter in (sampler_chat, sampler_tel):
                aclose = getattr(adapter, "aclose", None) if adapter is not None else None
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:
                        pass

    app = FastAPI(
        title="llm-cockpit",
        description="Multi-user web cockpit for Ollama (UC-02 dashboard slice).",
        lifespan=lifespan,
    )

    engine = make_engine(settings.db_url)
    session_factory = make_session_factory(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.rate_limiter = auth_router.RateLimiter()

    # UC-02 in-memory state.
    app.state.gpu_state = GpuSamplerState()
    app.state.model_state = ModelStateSamplerState()
    app.state.model_locks = defaultdict(asyncio.Lock)  # per-model single-flight
    app.state.host_perf_lock = asyncio.Lock()  # one perf-test at a time across models
    app.state.chat_factory = chat_fac
    app.state.telemetry_factory = tel_fac

    app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])
    app.include_router(dashboard_router.router, prefix="/api/dashboard", tags=["dashboard"])
    # UC-03 — dashboard history charts share the /api/dashboard prefix.
    app.include_router(
        dashboard_history_router.router,
        prefix="/api/dashboard",
        tags=["dashboard"],
    )
    app.include_router(
        admin_ollama_router.router, prefix="/api/admin/ollama", tags=["admin"]
    )
    # UC-06 admin user management.
    app.include_router(
        admin_users_router.router, prefix="/api/admin/users", tags=["admin"]
    )
    # UC-04 chat router carries its own `/api/...` prefixes on each route
    # (so it can host the shared `/api/models` picker alongside `/api/chat`),
    # so it's mounted at root prefix.
    app.include_router(chat_router.router, tags=["chat"])
    # UC-06 code working folder MUST be registered *before* the UC-05 code
    # router. The latter has a `/api/code/{conversation_id}` route whose
    # int-typed path param would otherwise match `/api/code/files` first
    # and 422 on "files" not parsing as an integer.
    app.include_router(
        code_files_router.router, prefix="/api/code/files", tags=["code"]
    )
    # UC-05 code router.
    app.include_router(code_router.router, tags=["code"])

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if FRONTEND_DIST_DIR.exists():
        app.mount(
            "/",
            StaticFiles(directory=str(FRONTEND_DIST_DIR), html=True),
            name="frontend",
        )
    else:
        log.warning(
            "frontend_dist not found at %s — / and /dashboard will 404. "
            "Run the wheel build to bundle the static assets.",
            FRONTEND_DIST_DIR,
        )

    return app


# Re-export the dependency providers so callers can keep importing from
# `cockpit.main` if they want; the canonical home is `cockpit.deps`.
__all__ = [
    "create_app",
    "get_session",
    "get_settings",
    "FRONTEND_DIST_DIR",
]
