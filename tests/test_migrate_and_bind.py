"""Coverage-bumping tests for `cockpit-admin migrate`, the bind prompt,
and the deferred-subcommand stubs.

These exercise paths the headline T-01..T-10 suite doesn't reach.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from sqlalchemy import text

from cockpit.cli import main
from cockpit.db import make_engine
from cockpit.services.bootstrap import (
    DEFAULT_HOST,
    InitOptions,
    _resolve_bind,
    run_init,
)
from tests.conftest import FakeOllamaState


def test_migrate_creates_schema_in_empty_data_dir(
    tmp_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["migrate", "--data-dir", str(tmp_data_dir)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "head" in captured.out

    db_url = f"sqlite:///{tmp_data_dir / 'cockpit.db'}"
    engine = make_engine(db_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
        assert row is not None
        assert row[0] == "0001"
    finally:
        engine.dispose()


def test_resolve_bind_prefers_cli_flag() -> None:
    out = _resolve_bind(InitOptions(bind="0.0.0.0"), existing_host=None)
    assert out == "0.0.0.0"


def test_resolve_bind_falls_back_to_default_when_non_interactive() -> None:
    out = _resolve_bind(
        InitOptions(non_interactive=True),
        existing_host=None,
    )
    assert out == DEFAULT_HOST


def test_resolve_bind_uses_existing_config_host() -> None:
    out = _resolve_bind(InitOptions(), existing_host="0.0.0.0")
    assert out == "0.0.0.0"


def test_resolve_bind_interactive_prompt_default() -> None:
    """Empty input → default localhost."""
    stdin = io.StringIO("\n")
    stdout = io.StringIO()
    out = _resolve_bind(InitOptions(), existing_host=None, stdin=stdin, stdout=stdout)
    assert out == "127.0.0.1"
    assert "localhost only" in stdout.getvalue()


def test_resolve_bind_interactive_prompt_choice_2() -> None:
    """Choice 2 → 0.0.0.0 + TLS reminder printed."""
    stdin = io.StringIO("2\n")
    stdout = io.StringIO()
    out = _resolve_bind(InitOptions(), existing_host=None, stdin=stdin, stdout=stdout)
    assert out == "0.0.0.0"
    assert "VPN" in stdout.getvalue()


def test_resolve_bind_explicit_ip_fall_through() -> None:
    """Any other input is treated as an explicit address."""
    stdin = io.StringIO("192.168.1.42\n")
    stdout = io.StringIO()
    out = _resolve_bind(InitOptions(), existing_host=None, stdin=stdin, stdout=stdout)
    assert out == "192.168.1.42"


def test_resolve_bind_env_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COCKPIT_HOST", "10.0.0.5")
    out = _resolve_bind(InitOptions(), existing_host=None)
    assert out == "10.0.0.5"


def test_init_run_init_via_function(
    fake_ollama: FakeOllamaState, tmp_data_dir: Path
) -> None:
    """Drive `run_init` directly to exercise the InitResult dataclass."""
    result = run_init(
        InitOptions(
            data_dir=tmp_data_dir,
            ollama_url=fake_ollama.url,
            admin_password="PWchange1",
            bind="127.0.0.1",
            non_interactive=True,
        )
    )
    assert result.bind_host == "127.0.0.1"
    assert not result.already_initialised
    assert "gemma3:27b" in result.discovered_models

    # Second call is the idempotent path → already_initialised=True.
    result2 = run_init(
        InitOptions(
            data_dir=tmp_data_dir,
            ollama_url=fake_ollama.url,
            non_interactive=True,
        )
    )
    assert result2.already_initialised


def test_systemd_install_stub_returns_deferred() -> None:
    rc = main(["systemd-install"])
    assert rc == 2


def test_user_set_role_stub_returns_deferred() -> None:
    rc = main(["user-set-role"])
    assert rc == 2
