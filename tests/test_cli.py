"""CLI surface tests: --version, --help, deferred stubs.

Maps to UC-08 Test Spec T-06, T-07.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from cockpit import __version__
from cockpit.cli import main


def test_version_prints_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """T-06 — `cockpit-admin --version` prints non-empty version + exits zero."""
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    captured = capsys.readouterr()
    assert exc.value.code == 0
    out = (captured.out + captured.err).strip()
    assert out
    assert __version__ in out


def test_serve_help_lists_required_flags(capsys: pytest.CaptureFixture[str]) -> None:
    """T-07 — `cockpit-admin serve --help` lists --host, --port, --config, --log-level."""
    with pytest.raises(SystemExit) as exc:
        main(["serve", "--help"])
    captured = capsys.readouterr()
    assert exc.value.code == 0
    text = captured.out + captured.err
    for flag in ("--host", "--port", "--config", "--log-level"):
        assert flag in text, f"`serve --help` missing {flag}; got:\n{text}"


def test_serve_stub_returns_deferred() -> None:
    """`serve` is not implemented in Slice A; expect exit code 2."""
    rc = main(["serve"])
    assert rc == 2


def test_user_add_stub_returns_deferred() -> None:
    """User-management subcommands are deferred to UC-06."""
    rc = main(["user-add"])
    assert rc == 2


def test_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    """Bare `cockpit-admin` with no subcommand should print help and exit 0."""
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 0
    assert "cockpit-admin" in captured.out


def test_console_script_entrypoint_runs() -> None:
    """Sanity: invoke the installed entry point via `python -m cockpit.cli`.

    This is a one-off check that the package is import-clean from a fresh
    interpreter — closest we can get to T-01's subprocess invocation without
    requiring a wheel install in the test environment.
    """
    result = subprocess.run(
        [sys.executable, "-m", "cockpit.cli", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert __version__ in (result.stdout + result.stderr)
