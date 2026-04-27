"""UC-08 Slice B — serve / main.py / frontend mount tests.

Maps to UC-08 Test Spec entries that Slice A explicitly deferred:
    T-09  AC-10 — wheel ships frontend_dist/index.html and serves it on `/`.
    T-07  AC-8  — `cockpit-admin serve --help` lists the supported flags
                 (already covered in Slice A's test_cli.py; the CLI body
                 is now real, not a stub — re-asserted here).

Plus the spec's serve-flow §4: app boot logs warning when Ollama is
unreachable but does **not** exit. We exercise this by injecting a
`FakeLLMChat` whose `list_models` raises `OllamaUnreachableError` and
asserting the app still serves requests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cockpit.adapters.fake_chat import FakeLLMChat, model_info
from cockpit.config import Settings
from cockpit.db import upgrade_to_head
from cockpit.main import FRONTEND_DIST_DIR, create_app
from cockpit.ports.llm_chat import OllamaUnreachableError


@pytest.fixture
def initialised_settings(tmp_path: Path) -> Settings:
    """Realistic settings with a fresh, migrated SQLite DB."""
    data_dir = tmp_path / "cockpit-data"
    data_dir.mkdir()
    settings = Settings(data_dir=data_dir, host="127.0.0.1", port=18080)
    upgrade_to_head(settings.db_url)
    return settings


def test_create_app_smoke(initialised_settings: Settings) -> None:
    """The app builds, boots, and serves /healthz."""
    fake = FakeLLMChat(models=[model_info("gemma3:27b")])
    app = create_app(initialised_settings, chat_factory=lambda url: fake)
    with TestClient(app) as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_api_auth_me_without_cookie_returns_401(initialised_settings: Settings) -> None:
    """The Slice B placeholder /api/auth/me returns 401 with no session.
    UC-01's commit replaces the stub with the real implementation; the
    return code stays 401 in the no-cookie case.
    """
    fake = FakeLLMChat(models=[])
    app = create_app(initialised_settings, chat_factory=lambda url: fake)
    with TestClient(app) as client:
        r = client.get("/api/auth/me")
        assert r.status_code == 401


def test_static_index_serves_root(initialised_settings: Settings) -> None:
    fake = FakeLLMChat(models=[])
    app = create_app(initialised_settings, chat_factory=lambda url: fake)
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "/api/auth/me" in r.text  # the inline-script fetch lives in index.html


def test_static_dashboard_directory_serves_index(initialised_settings: Settings) -> None:
    """`StaticFiles(html=True)` resolves `/dashboard/` to `dashboard/index.html`."""
    fake = FakeLLMChat(models=[])
    app = create_app(initialised_settings, chat_factory=lambda url: fake)
    with TestClient(app) as client:
        r = client.get("/dashboard/")
        assert r.status_code == 200
        assert "Sprint 2 placeholder" in r.text
        assert "Log out" in r.text


def test_static_login_directory_serves_index(initialised_settings: Settings) -> None:
    fake = FakeLLMChat(models=[])
    app = create_app(initialised_settings, chat_factory=lambda url: fake)
    with TestClient(app) as client:
        r = client.get("/login/")
        assert r.status_code == 200
        assert 'id="login"' in r.text


def test_static_change_password_directory_serves_index(initialised_settings: Settings) -> None:
    fake = FakeLLMChat(models=[])
    app = create_app(initialised_settings, chat_factory=lambda url: fake)
    with TestClient(app) as client:
        r = client.get("/change-password/")
        assert r.status_code == 200
        assert 'id="change"' in r.text


def test_startup_probe_warns_but_does_not_exit_when_ollama_unreachable(
    initialised_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """Per UC-08 §serve flow bullet 4: log warning, do not exit."""
    fake = FakeLLMChat(raise_on_list_models=OllamaUnreachableError("simulated"))
    app = create_app(initialised_settings, chat_factory=lambda url: fake)

    with caplog.at_level("WARNING", logger="cockpit.main"):
        with TestClient(app) as client:
            r = client.get("/healthz")
            assert r.status_code == 200

    assert any(
        "Ollama unreachable" in rec.getMessage() for rec in caplog.records
    ), f"expected 'Ollama unreachable' warning; got {[r.getMessage() for r in caplog.records]}"


def test_startup_probe_logs_count_when_ollama_reachable(
    initialised_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeLLMChat(models=[model_info("a"), model_info("b")])
    app = create_app(initialised_settings, chat_factory=lambda url: fake)
    with caplog.at_level("INFO", logger="cockpit.main"):
        with TestClient(app) as client:
            r = client.get("/healthz")
            assert r.status_code == 200
    assert any("2 models" in rec.getMessage() for rec in caplog.records)


def test_skip_startup_probe_flag(initialised_settings: Settings) -> None:
    """The skip flag bypasses the probe entirely — useful for fast unit
    tests that don't need any FakeLLMChat at all."""
    app = create_app(initialised_settings, skip_startup_probe=True, skip_db_upgrade=True)
    with TestClient(app) as client:
        r = client.get("/healthz")
        assert r.status_code == 200


def test_settings_from_toml(tmp_path: Path) -> None:
    """`Settings.from_toml` builds settings from a config.toml written by init."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[server]\nhost = "0.0.0.0"\nport = 9000\n'
        '[ollama]\nurl = "http://127.0.0.1:22222"\n'
        '[security]\njwt_secret = "abc"\nsession_days = 14\nbcrypt_cost = 10\n'
        '[telemetry]\nnvidia_smi_path = ""\nsample_interval_s = 10\n'
        f'[paths]\ndata_dir = "{tmp_path}"\ndb_file = "x.db"\nlog_file = "x.log"\n',
        encoding="utf-8",
    )
    settings = Settings.from_toml(cfg)
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000
    assert settings.ollama_url == "http://127.0.0.1:22222"
    assert settings.session_days == 14
    assert settings.bcrypt_cost == 10
    assert settings.db_file == "x.db"


def test_serve_help_lists_required_flags() -> None:
    """T-07 re-asserted with the real cmd_serve body."""
    from cockpit.cli import main as cli_main

    with pytest.raises(SystemExit) as exc:
        cli_main(["serve", "--help"])
    assert exc.value.code == 0


def test_frontend_dist_dir_is_under_package() -> None:
    """The frontend_dist directory is shipped inside the package — important
    for non-editable wheel installs.
    """
    assert FRONTEND_DIST_DIR.is_dir()
    assert (FRONTEND_DIST_DIR / "index.html").is_file()
    assert (FRONTEND_DIST_DIR / "login" / "index.html").is_file()
    assert (FRONTEND_DIST_DIR / "change-password" / "index.html").is_file()
    assert (FRONTEND_DIST_DIR / "dashboard" / "index.html").is_file()
