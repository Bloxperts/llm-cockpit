"""NvidiaSmiTelemetry — the only `Telemetry` adapter in v0.1.

Shells out to `nvidia-smi` and parses its CSV output. Per ADR-003 §5,
absence of the binary is not an error — `sample()` returns `None`. A
non-zero exit from a present binary raises `TelemetryUnavailableError`
so the dashboard can surface it as a soft warning rather than a silent
"no GPU" response.

Subprocess orchestration via `asyncio.create_subprocess_exec` so the
samplers don't block the event loop. No `subprocess.run`, no threads.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from cockpit.ports.telemetry import (
    GpuSnapshot,
    TelemetryUnavailableError,
)

log = logging.getLogger(__name__)

NVIDIA_SMI_QUERY = (
    "index",
    "memory.used",
    "memory.total",
    "temperature.gpu",
    "power.draw",
)
NVIDIA_SMI_FORMAT = "csv,noheader,nounits"
DEFAULT_BINARY = "nvidia-smi"
DEFAULT_TIMEOUT_S = 5.0


def _find_nvidia_smi(explicit_path: str | None = None) -> str | None:
    """Resolve the binary location: explicit path > $PATH lookup > None."""
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        if candidate.is_file():
            return str(candidate)
        return None
    return shutil.which(DEFAULT_BINARY)


def _parse_optional_float(value: str) -> float | None:
    """Parse a `nvidia-smi` field that may be `[N/A]`."""
    stripped = value.strip()
    if not stripped or stripped.lower() in ("[n/a]", "n/a", "[not supported]"):
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def _parse_int_field(value: str) -> int:
    return int(float(value.strip()))


def _parse_csv_line(line: str) -> GpuSnapshot:
    parts = [p.strip() for p in line.split(",")]
    if len(parts) != len(NVIDIA_SMI_QUERY):
        raise TelemetryUnavailableError(
            f"unexpected nvidia-smi column count ({len(parts)} != {len(NVIDIA_SMI_QUERY)}): {line!r}"
        )
    return GpuSnapshot(
        index=_parse_int_field(parts[0]),
        vram_used_mb=_parse_int_field(parts[1]),
        vram_total_mb=_parse_int_field(parts[2]),
        temp_c=_parse_optional_float(parts[3]),
        power_w=_parse_optional_float(parts[4]),
    )


class NvidiaSmiTelemetry:
    """`Telemetry` adapter for hosts with NVIDIA + `nvidia-smi`.

    Constructor arguments allow tests to inject a path or override the
    subprocess runner (`subprocess_runner` defaults to
    `asyncio.create_subprocess_exec`). In production neither argument is
    needed.
    """

    def __init__(
        self,
        *,
        binary_path: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        subprocess_runner=None,
    ) -> None:
        self._binary_path_hint = binary_path
        self._timeout_s = timeout_s
        self._subprocess_runner = subprocess_runner or asyncio.create_subprocess_exec

    async def sample(self) -> list[GpuSnapshot] | None:
        binary = _find_nvidia_smi(self._binary_path_hint)
        if binary is None:
            return None

        cmd = [
            binary,
            f"--query-gpu={','.join(NVIDIA_SMI_QUERY)}",
            f"--format={NVIDIA_SMI_FORMAT}",
        ]
        try:
            proc = await self._subprocess_runner(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return None

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout_s
            )
        except asyncio.TimeoutError as exc:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise TelemetryUnavailableError(
                f"nvidia-smi timed out after {self._timeout_s}s"
            ) from exc

        if proc.returncode != 0:
            raise TelemetryUnavailableError(
                f"nvidia-smi exited {proc.returncode}: "
                f"{stderr_b.decode('utf-8', errors='replace').strip()[:200]}"
            )

        stdout = stdout_b.decode("utf-8", errors="replace")
        snapshots: list[GpuSnapshot] = []
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            snapshots.append(_parse_csv_line(line))
        return snapshots
