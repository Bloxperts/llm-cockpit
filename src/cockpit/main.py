"""FastAPI app factory + lifespan.

Per UC-08 functional spec §serve flow:
1. Load config.toml (the CLI does this and passes us a `Settings`).
2. Ensure DB schema is current on startup (auto `upgrade head`).
3. Probe Ollama once via `LLMChat.list_models()`; log a warning if unreachable
   but **do not** exit. The dashboard's "Ollama unreachable" badge tells the
   operator (UC-02, Sprint 3).
4. Mount `frontend_dist/` as `StaticFiles(html=True)` for any path that
   isn't under `/api/`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, sessionmaker

from cockpit.config import Settings
from cockpit.db import make_engine, make_session_factory, upgrade_to_head
from cockpit.ports.llm_chat import LLMChat, OllamaResponseError, OllamaUnreachableError
from cockpit.routers import auth as auth_router

log = logging.getLogger(__name__)

ChatFactory = Callable[[str], LLMChat]
FRONTEND_DIST_DIR = Path(__file__).parent / "frontend_dist"


def _default_chat_factory(url: str) -> LLMChat:
    """Production factory — built lazily so importing main.py doesn't drag
    `httpx` into routers that don't use the port directly.
    """
    from cockpit.adapters.ollama_chat import OllamaLLMChat

    return OllamaLLMChat(url)


def create_app(
    settings: Settings | None = None,
    *,
    chat_factory: ChatFactory | None = None,
    skip_db_upgrade: bool = False,
    skip_startup_probe: bool = False,
) -> FastAPI:
    """Build a FastAPI app for `cockpit-admin serve` and tests.

    `chat_factory` is the DI seam for the startup Ollama probe — defaults to
    building `OllamaLLMChat`; tests inject `FakeLLMChat`. `skip_db_upgrade`
    and `skip_startup_probe` exist so unit tests can bypass network / FS
    side-effects when they've already prepared the DB or want a fast fixture.
    """
    if settings is None:
        settings = Settings()
    factory = chat_factory or _default_chat_factory

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not skip_db_upgrade:
            try:
                upgrade_to_head(settings.db_url)
            except Exception as exc:  # pragma: no cover — pathological
                log.warning("DB upgrade on startup failed: %s", exc)
        if not skip_startup_probe:
            chat = factory(settings.ollama_url)
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
        yield

    app = FastAPI(
        title="llm-cockpit",
        description="Multi-user web cockpit for Ollama (UC-08 part B placeholder dashboard).",
        lifespan=lifespan,
    )

    engine = make_engine(settings.db_url)
    session_factory = make_session_factory(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory

    app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])

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


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency: yield a SQLAlchemy session bound to the app's engine."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


# Re-exports so other modules don't reach into app.state directly.
__all__ = [
    "create_app",
    "get_session",
    "get_settings",
    "FRONTEND_DIST_DIR",
]
