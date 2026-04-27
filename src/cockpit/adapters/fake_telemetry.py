"""FakeTelemetry — the in-memory test seam for the `Telemetry` port.

Tests that depend on GPU snapshots inject this rather than mocking
`asyncio.create_subprocess_exec`. Mirrors `FakeLLMChat`'s shape (UC-07):
the same `last_call` / `calls` recorder semantics so dashboard tests can
assert that "the sampler called `Telemetry.sample()` exactly N times in
this window".
"""

from __future__ import annotations

from typing import Any

from cockpit.ports.telemetry import GpuSnapshot, TelemetryUnavailableError


class FakeTelemetry:
    """Configurable static stand-in for a `Telemetry` implementation."""

    def __init__(
        self,
        *,
        snapshots: list[GpuSnapshot] | None = None,
        return_none: bool = False,
        raise_unavailable: bool = False,
    ) -> None:
        self._snapshots = list(snapshots) if snapshots is not None else []
        self._return_none = return_none
        self._raise_unavailable = raise_unavailable

        self.last_call: dict[str, Any] | None = None
        self.calls: list[dict[str, Any]] = []

    def _record(self, **kwargs: Any) -> None:
        entry = {"method": "sample", **kwargs}
        self.last_call = entry
        self.calls.append(entry)

    async def sample(self) -> list[GpuSnapshot] | None:
        self._record()
        if self._raise_unavailable:
            raise TelemetryUnavailableError("FakeTelemetry: simulated unavailable")
        if self._return_none:
            return None
        return list(self._snapshots)

    async def aclose(self) -> None:
        """No-op aclose so callers can treat the fake like the real adapter."""
        # Use a distinct marker so tests can tell apart sample/aclose if needed.
        entry = {"method": "aclose"}
        self.last_call = entry
        self.calls.append(entry)


def gpu_snapshot(
    index: int,
    *,
    vram_used_mb: int = 8000,
    vram_total_mb: int = 24000,
    temp_c: float | None = 65.0,
    power_w: float | None = 200.0,
) -> GpuSnapshot:
    """Convenience factory used in tests."""
    return GpuSnapshot(
        index=index,
        vram_used_mb=vram_used_mb,
        vram_total_mb=vram_total_mb,
        temp_c=temp_c,
        power_w=power_w,
    )
