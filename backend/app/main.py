"""llm-cockpit FastAPI entrypoint.

This is a scaffold. Real handlers in the routers/ package will land per spec.
"""
from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("cockpit")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("llm-cockpit starting on %s:%d", settings.host, settings.port)
    log.info("scheduler_url=%s ollama_url=%s", settings.scheduler_url, settings.ollama_url)
    # TODO: boot SQLite, run alembic upgrade head, start telemetry sampler
    yield
    log.info("llm-cockpit shutting down")


app = FastAPI(title="llm-cockpit", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://192.168.111.200:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/dashboard/snapshot")
async def dashboard_snapshot():
    """STUB. Implementation per SPEC-002."""
    return {"status": "STUB", "spec": "SPEC-002"}


# TODO: include_router for auth, dashboard, chat, code, admin
