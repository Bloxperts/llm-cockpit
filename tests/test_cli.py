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


def test_serve_invokes_uvicorn_with_resolved_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """`cockpit-admin serve` builds the app and hands it to uvicorn.run.

    We assert the resolution chain rather than actually start a server:
        - missing config.toml → fall back to env / defaults
        - --host / --port flags override
    """
    captured: dict = {}

    def fake_run(app, host, port, log_level):
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port
        captured["log_level"] = log_level

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    rc = main(
        [
            "serve",
            "--data-dir", str(tmp_path),
            "--host", "127.0.0.1",
            "--port", "0",
            "--log-level", "WARNING",
        ]
    )
    assert rc == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 0
    assert captured["log_level"] == "warning"


def test_serve_loads_config_toml_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """If config.toml exists, settings come from it; CLI flags still override."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[server]\nhost = "0.0.0.0"\nport = 9090\n'
        '[ollama]\nurl = "http://127.0.0.1:11434"\n'
        '[security]\njwt_secret = "x"\nsession_days = 7\nbcrypt_cost = 4\n'
        '[telemetry]\nnvidia_smi_path = ""\nsample_interval_s = 5\n'
        f'[paths]\ndata_dir = "{tmp_path}"\ndb_file = "x.db"\nlog_file = "x.log"\n',
        encoding="utf-8",
    )
    captured: dict = {}

    def fake_run(app, host, port, log_level):
        captured["host"] = host
        captured["port"] = port

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    # No --host/--port — expect 0.0.0.0 and 9090 from config.toml.
    rc = main(["serve", "--config", str(cfg)])
    assert rc == 0
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9090


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
