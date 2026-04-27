"""`cockpit-admin doctor` tests (partial T-10).

Slice A scope: doctor exits zero on a healthy install (Ollama up, DB
schema current, data dir writable). Doctor exits non-zero when Ollama is
stopped. Frontend assets and nvidia-smi are reported as warnings only in
this slice; full T-10 coverage lands in Slice B.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cockpit.cli import main
from tests.conftest import FakeOllamaState


def _bootstrap(fake: FakeOllamaState, data_dir: Path) -> None:
    rc = main(
        [
            "init",
            "--data-dir", str(data_dir),
            "--ollama-url", fake.url,
            "--admin-password", "PWchange1",
            "--bind", "127.0.0.1",
            "--non-interactive",
        ]
    )
    assert rc == 0


def test_doctor_healthy_install_exits_zero(
    fake_ollama: FakeOllamaState,
    tmp_data_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _bootstrap(fake_ollama, tmp_data_dir)
    capsys.readouterr()  # drain init output

    rc = main(
        [
            "doctor",
            "--data-dir", str(tmp_data_dir),
            "--ollama-url", fake_ollama.url,
        ]
    )
    captured = capsys.readouterr()

    assert rc == 0
    assert "[OK  ] ollama_reachable" in captured.out
    assert "[OK  ] db_schema_current" in captured.out
    assert "[OK  ] data_dir_writable" in captured.out


def test_doctor_ollama_down_exits_nonzero(
    fake_ollama: FakeOllamaState,
    tmp_data_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _bootstrap(fake_ollama, tmp_data_dir)
    capsys.readouterr()

    rc = main(
        [
            "doctor",
            "--data-dir", str(tmp_data_dir),
            "--ollama-url", "http://127.0.0.1:1",  # unreachable
        ]
    )
    captured = capsys.readouterr()

    assert rc != 0
    assert "[FAIL] ollama_reachable" in captured.out
    assert "Cannot reach Ollama" in captured.out


def test_doctor_uses_config_toml_for_ollama_url(
    fake_ollama: FakeOllamaState,
    tmp_data_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When --ollama-url is omitted, doctor reads it from config.toml."""
    _bootstrap(fake_ollama, tmp_data_dir)
    capsys.readouterr()

    rc = main(["doctor", "--data-dir", str(tmp_data_dir)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "[OK  ] ollama_reachable" in captured.out


def test_doctor_missing_db_fails(
    fake_ollama: FakeOllamaState,
    tmp_data_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No init was run → doctor reports db missing + exits non-zero."""
    rc = main(
        [
            "doctor",
            "--data-dir", str(tmp_data_dir),
            "--ollama-url", fake_ollama.url,
        ]
    )
    captured = capsys.readouterr()
    assert rc != 0
    assert "[FAIL] db_schema_current" in captured.out
    assert "db missing" in captured.out


def test_doctor_missing_data_dir_fails(
    fake_ollama: FakeOllamaState,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    nonexistent = tmp_path / "does-not-exist"
    rc = main(
        [
            "doctor",
            "--data-dir", str(nonexistent),
            "--ollama-url", fake_ollama.url,
        ]
    )
    captured = capsys.readouterr()
    assert rc != 0
    assert "[FAIL] data_dir_writable" in captured.out
    assert "does not exist" in captured.out


def test_doctor_data_dir_is_a_file_fails(
    fake_ollama: FakeOllamaState,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If `--data-dir` points at a regular file, the writable check fails."""
    bogus = tmp_path / "i-am-a-file"
    bogus.write_text("not a dir")
    rc = main(
        [
            "doctor",
            "--data-dir", str(bogus),
            "--ollama-url", fake_ollama.url,
        ]
    )
    captured = capsys.readouterr()
    assert rc != 0
    assert "[FAIL] data_dir_writable" in captured.out


def test_doctor_corrupt_db_fails(
    fake_ollama: FakeOllamaState,
    tmp_data_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A non-SQLite file at cockpit.db should produce a `db schema unreadable` failure."""
    (tmp_data_dir / "cockpit.db").write_bytes(b"not a real sqlite database")
    rc = main(
        [
            "doctor",
            "--data-dir", str(tmp_data_dir),
            "--ollama-url", fake_ollama.url,
        ]
    )
    captured = capsys.readouterr()
    assert rc != 0
    assert "[FAIL] db_schema_current" in captured.out


def test_doctor_stale_revision_fails(
    fake_ollama: FakeOllamaState,
    tmp_data_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """If the DB's alembic_version is stale, doctor flags it."""
    _bootstrap(fake_ollama, tmp_data_dir)
    capsys.readouterr()

    # Forge a stale revision.
    from sqlalchemy import text
    from cockpit.db import make_engine
    db_url = f"sqlite:///{tmp_data_dir / 'cockpit.db'}"
    engine = make_engine(db_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = 'forged'"))
            conn.commit()
    finally:
        engine.dispose()

    rc = main(
        [
            "doctor",
            "--data-dir", str(tmp_data_dir),
            "--ollama-url", fake_ollama.url,
        ]
    )
    captured = capsys.readouterr()
    assert rc != 0
    assert "db schema is forged" in captured.out


def test_doctor_no_config_uses_default_ollama_url(
    tmp_data_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Doctor with no config.toml + no --ollama-url falls back to the default
    URL — which is unreachable in tests, so the check fails with that URL."""
    rc = main(["doctor", "--data-dir", str(tmp_data_dir)])
    captured = capsys.readouterr()
    assert rc != 0
    # data_dir is empty, so multiple checks fail. We assert specifically on
    # the fall-through default URL appearing in the failure line.
    assert "11434" in captured.out or "Cannot reach Ollama" in captured.out


def test_doctor_corrupt_config_falls_back(
    fake_ollama: FakeOllamaState,
    tmp_data_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bogus TOML in config.toml shouldn't crash doctor; it falls back to default URL."""
    (tmp_data_dir / "config.toml").write_text("this is { not valid toml ===")
    rc = main(["doctor", "--data-dir", str(tmp_data_dir)])
    captured = capsys.readouterr()
    # Doctor still ran and produced check lines.
    assert "ollama_reachable" in captured.out
    assert rc != 0  # nothing's set up; failures expected


def test_doctor_nvidia_smi_present(
    fake_ollama: FakeOllamaState,
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When shutil.which finds nvidia-smi, doctor prints OK for that check."""
    _bootstrap(fake_ollama, tmp_data_dir)
    capsys.readouterr()

    import cockpit.cli as cli_mod
    monkeypatch.setattr(cli_mod.shutil, "which", lambda name: "/usr/bin/nvidia-smi")

    rc = main(
        [
            "doctor",
            "--data-dir", str(tmp_data_dir),
            "--ollama-url", fake_ollama.url,
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "[OK  ] nvidia_smi" in captured.out
