"""`cockpit-admin init` flow tests.

Maps to UC-08 Test Spec:
    T-01 — happy path against a fake Ollama.
    T-02 — Ollama unreachable → non-zero exit, "Cannot reach Ollama" on stderr.
    T-03 — re-run on existing data dir → "already initialised", admin untouched.
    T-04 — DB has exactly one user (admin / role=admin / must_change_password=1).
    T-05 — model_tags has rows tagging gemma3:27b=chat, qwen3-coder:30b=code.
    T-08 — `init` does not invoke sudo.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from sqlalchemy import text

from cockpit.cli import main
from cockpit.db import make_engine, make_session_factory, session_scope
from cockpit.models import ModelTag, User
from cockpit.services.users import verify_password
from tests.conftest import FakeOllamaState


def test_init_happy_path(
    fake_ollama: FakeOllamaState,
    tmp_data_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """T-01 + T-04 + T-05 combined.

    A clean run produces: config.toml, cockpit.db with the admin row, and
    model_tags rows for the two models the fake Ollama advertises.
    """
    rc = main(
        [
            "init",
            "--data-dir", str(tmp_data_dir),
            "--ollama-url", fake_ollama.url,
            "--admin-password", "PWchange1",
            "--bind", "127.0.0.1",
            "--non-interactive",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0, f"init failed: {captured.out}\n{captured.err}"

    # Files exist.
    assert (tmp_data_dir / "config.toml").exists()
    assert (tmp_data_dir / "cockpit.db").exists()

    # T-04: exactly one user row, admin / admin / must_change_password=1.
    db_url = f"sqlite:///{tmp_data_dir / 'cockpit.db'}"
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with session_scope(factory) as session:
        users = session.query(User).all()
        assert len(users) == 1
        admin = users[0]
        assert admin.username == "admin"
        assert admin.role == "admin"
        assert admin.must_change_password == 1
        assert verify_password("PWchange1", admin.pw_hash)

        # T-05: tags.
        tags = {t.model: t.tag for t in session.query(ModelTag).all()}
    assert tags == {"gemma3:27b": "chat", "qwen3-coder:30b": "code"}


def test_init_ollama_unreachable_fast_exit(
    tmp_data_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """T-02 — non-zero exit within 5 s, stderr contains "Cannot reach Ollama"."""
    start = time.monotonic()
    rc = main(
        [
            "init",
            "--data-dir", str(tmp_data_dir),
            # 127.0.0.1:1 is reserved/unreachable in practice
            "--ollama-url", "http://127.0.0.1:1",
            "--admin-password", "PWchange1",
            "--bind", "127.0.0.1",
            "--non-interactive",
        ]
    )
    elapsed = time.monotonic() - start
    captured = capsys.readouterr()

    assert rc != 0
    assert elapsed < 8.0, f"init took {elapsed:.1f}s, must be under 5 s + slack"
    assert "Cannot reach Ollama" in captured.err

    # No database created — we bail before touching the disk.
    assert not (tmp_data_dir / "cockpit.db").exists()


def test_init_idempotent(
    fake_ollama: FakeOllamaState,
    tmp_data_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """T-03 — second run prints "already initialised" and leaves admin password alone."""
    args = [
        "init",
        "--data-dir", str(tmp_data_dir),
        "--ollama-url", fake_ollama.url,
        "--admin-password", "PWchange1",
        "--bind", "127.0.0.1",
        "--non-interactive",
    ]
    assert main(args) == 0
    capsys.readouterr()  # drain

    # Second run with a different --admin-password must NOT overwrite.
    args[args.index("PWchange1")] = "DifferentPW2"
    rc = main(args)
    captured = capsys.readouterr()

    assert rc == 0
    assert "already initialised" in captured.out

    # Original password still works.
    db_url = f"sqlite:///{tmp_data_dir / 'cockpit.db'}"
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with session_scope(factory) as session:
        admin = session.query(User).filter_by(username="admin").one()
        assert verify_password("PWchange1", admin.pw_hash)
        assert not verify_password("DifferentPW2", admin.pw_hash)


def test_init_does_not_invoke_sudo(
    fake_ollama: FakeOllamaState,
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-08 — patch subprocess.run; assert no call uses 'sudo'."""
    import subprocess as _sp

    calls: list[list[str]] = []
    real_run = _sp.run

    def recording_run(cmd, *args, **kwargs):
        calls.append(list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)])
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(_sp, "run", recording_run)

    rc = main(
        [
            "init",
            "--data-dir", str(tmp_data_dir),
            "--ollama-url", fake_ollama.url,
            "--admin-password", "PWchange1",
            "--bind", "127.0.0.1",
            "--non-interactive",
        ]
    )
    assert rc == 0

    for cmd in calls:
        assert not any("sudo" in part for part in cmd), \
            f"init invoked sudo: {cmd}"


def test_init_alembic_revision_recorded(
    fake_ollama: FakeOllamaState,
    tmp_data_dir: Path,
) -> None:
    """After a successful init, alembic_version.version_num exists."""
    rc = main(
        [
            "init",
            "--data-dir", str(tmp_data_dir),
            "--ollama-url", fake_ollama.url,
            "--admin-password", "PWchange1",
            "--bind", "127.0.0.1",
            "--non-interactive",
        ]
    )
    assert rc == 0

    db_url = f"sqlite:///{tmp_data_dir / 'cockpit.db'}"
    engine = make_engine(db_url)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
    assert row is not None
    # Track the moving head — UC-08 Slice A → 0001, UC-02 → 0002, etc.
    from cockpit.db import head_revision
    assert row[0] == head_revision()
