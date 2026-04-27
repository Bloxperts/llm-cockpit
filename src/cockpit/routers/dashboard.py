"""Dashboard router — snapshot + SSE stream.

Per UC-02 functional spec §API:

    GET /api/dashboard/snapshot  → DashboardSnapshot
    GET /api/dashboard/stream    → SSE; same shape every 5 s

Both endpoints are gated by `current_user_must_be_settled` (UC-09): a user
who hasn't completed their forced password change can't see the dashboard.

The snapshot endpoint reads from `app.state.gpu_state` + `app.state.model_state`
populated by the two background samplers in `cockpit.main`'s lifespan, so
each request is a near-instant read of pre-computed state — no synchronous
calls to Ollama or `nvidia-smi` on the request path.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from cockpit.deps import get_session
from cockpit.models import User
from cockpit.routers.auth import current_user_must_be_settled
from cockpit.schemas import DashboardSnapshot
from cockpit.services.metrics import assemble_dashboard_snapshot

log = logging.getLogger(__name__)
router = APIRouter()

# UC-02 §refresh cadence: GPU strip + columns every 5 s.
STREAM_INTERVAL_S = 5.0


def _build_snapshot_dict(request: Request, db: Session) -> dict:
    return assemble_dashboard_snapshot(
        session=db,
        gpu_state=request.app.state.gpu_state,
        model_state=request.app.state.model_state,
        # TODO(UC-04): once the chat router writes `messages`, surface the
        # last 20 calls (filtered by user role: admin sees all, others see
        # their own).
        last_calls=[],
    )


@router.get(
    "/snapshot",
    response_model=DashboardSnapshot,
    summary="Current dashboard snapshot.",
)
def get_snapshot(
    request: Request,
    user: User = Depends(current_user_must_be_settled),
    db: Session = Depends(get_session),
) -> DashboardSnapshot:
    return DashboardSnapshot.model_validate(_build_snapshot_dict(request, db))


async def stream_event_generator(
    request: Request,
    *,
    interval_s: float = STREAM_INTERVAL_S,
) -> AsyncIterator[dict]:
    """SSE event generator factored out so unit tests can iterate it
    without going through Starlette's TestClient streaming layer.
    """
    session_factory = request.app.state.session_factory
    while True:
        if await request.is_disconnected():
            return
        with session_factory() as session:
            payload = _build_snapshot_dict(request, session)
        yield {"event": "snapshot", "data": json.dumps(payload, default=str)}
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            return


@router.get("/stream", summary="Live dashboard SSE stream.")
def stream(
    request: Request,
    user: User = Depends(current_user_must_be_settled),
) -> EventSourceResponse:
    """SSE: emit a snapshot now and again every `STREAM_INTERVAL_S` seconds.

    A new SQLAlchemy session is opened per emit — sessions don't survive a
    long-running async generator cleanly, and the snapshot assembler's DB
    work is short.
    """
    return EventSourceResponse(stream_event_generator(request))
