"""UC-02 — Telemetry port + NvidiaSmiTelemetry + FakeTelemetry tests.

Maps to docs/specs/test/UC-02-dashboard-live.md T-01..T-05.

We never invoke a real `nvidia-smi`; subprocess orchestration is mocked
via a `subprocess_runner` parameter that the adapter accepts in its
constructor. Same DI seam pattern used by UC-07's `OllamaLLMChat` for
its `httpx.AsyncClient`.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cockpit.adapters.fake_telemetry import FakeTelemetry, gpu_snapshot
from cockpit.adapters.telemetry import (
    NVIDIA_SMI_FORMAT,
    NVIDIA_SMI_QUERY,
    NvidiaSmiTelemetry,
    _parse_csv_line,
    _parse_optional_float,
)
from cockpit.ports.telemetry import (
    GpuSnapshot,
    Telemetry,
    TelemetryUnavailableError,
)


def _proc_returning(stdout: bytes, *, returncode: int = 0, stderr: bytes = b"") -> AsyncMock:
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = MagicMock()
    return proc


# --- T-01 happy-path CSV parsing ------------------------------------------


@pytest.mark.asyncio
async def test_sample_parses_canonical_two_gpu_csv(tmp_path) -> None:
    """Two-GPU output: index, mem_used, mem_total, temp, power per row."""
    fake_binary = tmp_path / "nvidia-smi"
    fake_binary.touch()
    fake_binary.chmod(0o755)

    runner = AsyncMock(
        return_value=_proc_returning(
            b"0, 14530, 24576, 71, 240\n1, 22195, 24576, 75, 290\n"
        )
    )
    adapter = NvidiaSmiTelemetry(
        binary_path=str(fake_binary), subprocess_runner=runner
    )
    snapshots = await adapter.sample()

    assert snapshots == [
        GpuSnapshot(index=0, vram_used_mb=14530, vram_total_mb=24576, temp_c=71.0, power_w=240.0),
        GpuSnapshot(index=1, vram_used_mb=22195, vram_total_mb=24576, temp_c=75.0, power_w=290.0),
    ]
    # Confirm the runner saw the right argv shape.
    args = runner.call_args.args
    assert args[0] == str(fake_binary)
    assert any(a.startswith("--query-gpu=") for a in args[1:])
    assert f"--format={NVIDIA_SMI_FORMAT}" in args
    fields = ",".join(NVIDIA_SMI_QUERY)
    assert any(a == f"--query-gpu={fields}" for a in args)


# --- T-02 [N/A] columns ---------------------------------------------------


@pytest.mark.asyncio
async def test_sample_handles_n_a_columns(tmp_path) -> None:
    """`temp_c` and `power_w` resolve to None when nvidia-smi reports [N/A]."""
    fake_binary = tmp_path / "nvidia-smi"
    fake_binary.touch()
    fake_binary.chmod(0o755)

    runner = AsyncMock(
        return_value=_proc_returning(b"0, 14530, 24576, [N/A], [N/A]\n")
    )
    adapter = NvidiaSmiTelemetry(
        binary_path=str(fake_binary), subprocess_runner=runner
    )
    snapshots = await adapter.sample()
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.vram_used_mb == 14530
    assert snap.temp_c is None
    assert snap.power_w is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("[N/A]", None),
        ("n/a", None),
        ("", None),
        ("[Not Supported]", None),
        ("75", 75.0),
        ("12.5", 12.5),
        ("garbage", None),
    ],
)
def test_parse_optional_float_edge_cases(raw: str, expected) -> None:
    assert _parse_optional_float(raw) == expected


def test_parse_csv_line_rejects_unexpected_column_count() -> None:
    with pytest.raises(TelemetryUnavailableError):
        _parse_csv_line("only, three, columns")


# --- T-03 binary not found returns None -----------------------------------


@pytest.mark.asyncio
async def test_sample_returns_none_when_binary_missing() -> None:
    """No nvidia-smi on PATH → sample() returns None (not an exception).

    Per ADR-003 §5: GPU is optional. Macs / CPU-only hosts are first-class.
    """
    runner = AsyncMock()
    with patch("cockpit.adapters.telemetry.shutil.which", return_value=None):
        adapter = NvidiaSmiTelemetry(subprocess_runner=runner)
        result = await adapter.sample()
    assert result is None
    runner.assert_not_called()


@pytest.mark.asyncio
async def test_sample_returns_none_when_subprocess_raises_file_not_found(tmp_path) -> None:
    """If shutil.which finds it but exec races and hits FileNotFoundError,
    we still return None — same UX, same defensive behaviour."""
    fake_binary = tmp_path / "nvidia-smi"
    fake_binary.touch()
    runner = AsyncMock(side_effect=FileNotFoundError("vanished"))
    adapter = NvidiaSmiTelemetry(
        binary_path=str(fake_binary), subprocess_runner=runner
    )
    assert await adapter.sample() is None


@pytest.mark.asyncio
async def test_sample_returns_none_when_explicit_path_is_a_directory(tmp_path) -> None:
    bogus = tmp_path / "im-a-directory"
    bogus.mkdir()
    adapter = NvidiaSmiTelemetry(binary_path=str(bogus))
    assert await adapter.sample() is None


# --- T-04 non-zero exit raises TelemetryUnavailableError ------------------


@pytest.mark.asyncio
async def test_sample_raises_on_non_zero_exit(tmp_path) -> None:
    fake_binary = tmp_path / "nvidia-smi"
    fake_binary.touch()
    fake_binary.chmod(0o755)
    runner = AsyncMock(
        return_value=_proc_returning(b"", returncode=1, stderr=b"oops")
    )
    adapter = NvidiaSmiTelemetry(
        binary_path=str(fake_binary), subprocess_runner=runner
    )
    with pytest.raises(TelemetryUnavailableError) as exc:
        await adapter.sample()
    assert "exited 1" in str(exc.value)
    assert "oops" in str(exc.value)


@pytest.mark.asyncio
async def test_sample_times_out(tmp_path) -> None:
    fake_binary = tmp_path / "nvidia-smi"
    fake_binary.touch()
    fake_binary.chmod(0o755)

    proc = MagicMock()

    async def hang(*args, **kwargs):
        await asyncio.sleep(10)

    proc.communicate = AsyncMock(side_effect=hang)
    proc.kill = MagicMock()
    runner = AsyncMock(return_value=proc)

    adapter = NvidiaSmiTelemetry(
        binary_path=str(fake_binary), subprocess_runner=runner, timeout_s=0.05
    )
    with pytest.raises(TelemetryUnavailableError) as exc:
        await adapter.sample()
    assert "timed out" in str(exc.value)
    proc.kill.assert_called()


# --- T-05 FakeTelemetry recorder ------------------------------------------


@pytest.mark.asyncio
async def test_fake_returns_canned_snapshots_and_records_call() -> None:
    fake = FakeTelemetry(
        snapshots=[gpu_snapshot(0, vram_used_mb=1000), gpu_snapshot(1, vram_used_mb=2000)]
    )
    out = await fake.sample()
    assert [s.index for s in out] == [0, 1]
    assert fake.last_call == {"method": "sample"}
    assert len(fake.calls) == 1

    await fake.sample()
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_fake_can_simulate_no_gpu_host() -> None:
    fake = FakeTelemetry(return_none=True)
    assert await fake.sample() is None
    assert fake.last_call == {"method": "sample"}


@pytest.mark.asyncio
async def test_fake_can_simulate_unavailable() -> None:
    fake = FakeTelemetry(raise_unavailable=True)
    with pytest.raises(TelemetryUnavailableError):
        await fake.sample()


def test_fake_satisfies_protocol() -> None:
    """`FakeTelemetry` is structurally a `Telemetry` — runtime_checkable Protocol."""
    fake = FakeTelemetry()
    assert isinstance(fake, Telemetry)


# --- Grep-style: telemetry boundary ---------------------------------------


def test_no_subprocess_calls_outside_adapters() -> None:
    """Mirrors UC-07 AC-1 for telemetry: only `cockpit/adapters/telemetry.py`
    may shell out to nvidia-smi or use asyncio.subprocess primitives.
    """
    import ast
    from pathlib import Path

    src_root = Path(__file__).resolve().parent.parent / "src" / "cockpit"
    offenders: list[str] = []
    forbidden_modules = {"asyncio.subprocess"}
    forbidden_attrs = {"create_subprocess_exec", "create_subprocess_shell"}
    for path in src_root.rglob("*.py"):
        if "/adapters/" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                offenders.append(f"{path}:{node.lineno} from {node.module}")
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
                offenders.append(f"{path}:{node.lineno} {node.attr}")
    assert offenders == [], (
        "Telemetry boundary leak: subprocess primitives outside adapters/:\n"
        + "\n".join(offenders)
    )
