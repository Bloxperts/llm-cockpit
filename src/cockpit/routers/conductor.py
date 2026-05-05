"""Read-only Conductor dashboard endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from cockpit.config import Settings
from cockpit.deps import get_settings
from cockpit.models import User
from cockpit.routers.auth import require_role
from cockpit.services.conductor import ConductorPaths, ConductorSnapshot, degraded_response

router = APIRouter()
SETTINGS_DEPENDENCY = Depends(get_settings)
ADMIN_DEPENDENCY = Depends(require_role("admin"))


@router.get("/overview")
async def overview(
    settings: Settings = SETTINGS_DEPENDENCY,
    _user: User = ADMIN_DEPENDENCY,
) -> dict:
    if not settings.conductor_enabled:
        return degraded_response(RuntimeError("conductor_dashboard_disabled"))
    try:
        return _snapshot(settings).overview()
    except Exception as exc:  # noqa: BLE001 - endpoint must degrade instead of breaking cockpit.
        return degraded_response(exc)


@router.get("/context-report")
async def context_report(
    settings: Settings = SETTINGS_DEPENDENCY,
    _user: User = ADMIN_DEPENDENCY,
) -> dict:
    if not settings.conductor_enabled:
        return degraded_response(RuntimeError("conductor_dashboard_disabled"))
    try:
        return _snapshot(settings).context_report()
    except Exception as exc:  # noqa: BLE001 - endpoint must degrade instead of breaking cockpit.
        return degraded_response(exc)


def _snapshot(settings: Settings) -> ConductorSnapshot:
    return ConductorSnapshot(
        ConductorPaths(
            ssh_host=settings.conductor_ssh_host,
            manifest_path=settings.conductor_manifest_path,
            context_report_path=settings.conductor_context_report_path,
            timeout_seconds=settings.conductor_ssh_timeout_seconds,
        )
    )
