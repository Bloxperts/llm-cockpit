"""FastAPI dependency providers shared across routers.

Lives in its own module so routers can `Depends(get_session)` without a
circular import back into `cockpit.main`.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from cockpit.config import Settings


def get_session(request: Request) -> Iterator[Session]:
    """Yield a SQLAlchemy session bound to the app's engine. The session is
    closed in `finally` regardless of route outcome.
    """
    factory: sessionmaker[Session] = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_chat_factory(request: Request):
    """Return the LLMChat factory configured for the app (`url -> LLMChat`).
    Routers that issue ad-hoc Ollama calls (placement warm-up, perf harness,
    pull, delete) build their own adapter via this factory and `aclose()`
    when done.
    """
    return request.app.state.chat_factory


def get_telemetry_factory(request: Request):
    """Return the Telemetry factory (`() -> Telemetry`). Routers that need
    a one-shot snapshot (e.g. placement-transition mismatch detection)
    build an adapter via this factory.
    """
    return request.app.state.telemetry_factory
