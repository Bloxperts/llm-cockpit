"""Telemetry — the cockpit's optional outbound port for GPU samples.

Per UC-02 functional spec §DG-004 + ADR-003 §5: telemetry is **optional**.
Absence of `nvidia-smi` is not an error; the adapter returns `None` and the
dashboard renders an empty GPU strip.

Exception hierarchy lives here (not on the adapter) so callers can `except`
on contract-level errors without importing the concrete adapter — DP-029
hexagonal compliance, mirrors UC-07's `LLMChat` port pattern.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class GpuSnapshot:
    index: int
    vram_used_mb: int
    vram_total_mb: int
    temp_c: float | None
    power_w: float | None
    # Sprint 5b: configured power cap (`nvidia-smi --query-gpu=power.limit`).
    # Static for the lifetime of the GPU power-cap setting; the dashboard
    # divides current `power_w` by this to colour the watts indicator.
    # Optional with a default so existing call sites keep compiling.
    max_power_w: int | None = None


@runtime_checkable
class Telemetry(Protocol):
    """The optional outbound port. v0.1's only adapter is `NvidiaSmiTelemetry`."""

    async def sample(self) -> list[GpuSnapshot] | None:
        """Return GPU snapshots, or `None` when telemetry is unavailable
        (e.g. `nvidia-smi` not on PATH). `None` is the normal "no GPU"
        response, not an error.
        """
        ...


# --- Exceptions ------------------------------------------------------------


class TelemetryError(Exception):
    """Base class — never raised directly; concrete subtypes always do."""


class TelemetryUnavailableError(TelemetryError):
    """`nvidia-smi` was found but exited non-zero or produced unparseable output.

    Distinct from `sample() → None`, which is the normal "no telemetry" case.
    `TelemetryUnavailableError` means we *expected* telemetry to work and it
    didn't — the dashboard should surface this as a soft warning, not flip
    the GPU strip to "no GPU".
    """
