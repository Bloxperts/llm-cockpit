"""Cockpit settings.

Resolution order (highest precedence first), per ADR-002 v1.1 + COMPONENTS.md §6:

    explicit kwarg > COCKPIT_* env > config.toml > OLLAMA_HOST env (Ollama URL only) > default

The CLI passes a `Settings` instance built from `Settings.load()`; tests
construct one directly.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_BCRYPT_COST = 12
DEFAULT_SESSION_DAYS = 7
DEFAULT_SAMPLE_INTERVAL_S = 5


def default_data_dir() -> Path:
    """`$COCKPIT_DATA_DIR > $XDG_DATA_HOME/llm-cockpit > ~/.local/share/llm-cockpit`."""
    env = os.environ.get("COCKPIT_DATA_DIR")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "llm-cockpit"
    return Path.home() / ".local" / "share" / "llm-cockpit"


def default_ollama_url() -> str:
    return os.environ.get("COCKPIT_OLLAMA_URL") or os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_URL


class Settings(BaseSettings):
    """Process-wide settings.

    Mirrors `[server]`, `[ollama]`, `[security]`, `[telemetry]`, `[paths]` from
    the generated `config.toml` (UC-08 §Default config.toml).
    """

    model_config = SettingsConfigDict(env_prefix="COCKPIT_", extra="ignore")

    # [paths]
    data_dir: Path = Field(default_factory=default_data_dir)
    db_file: str = "cockpit.db"
    log_file: str = "cockpit.log"

    # [server]
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    # [ollama]
    ollama_url: str = Field(default_factory=default_ollama_url)

    # [security]
    jwt_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    session_days: int = DEFAULT_SESSION_DAYS
    bcrypt_cost: int = DEFAULT_BCRYPT_COST

    # [telemetry]
    nvidia_smi_path: str = ""
    sample_interval_s: int = DEFAULT_SAMPLE_INTERVAL_S

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_file

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


@dataclass
class TomlConfig:
    """In-memory representation of `config.toml`. Round-trips via tomllib + tomli_w."""

    server_host: str = DEFAULT_HOST
    server_port: int = DEFAULT_PORT
    ollama_url: str = DEFAULT_OLLAMA_URL
    jwt_secret: str = ""
    session_days: int = DEFAULT_SESSION_DAYS
    bcrypt_cost: int = DEFAULT_BCRYPT_COST
    nvidia_smi_path: str = ""
    sample_interval_s: int = DEFAULT_SAMPLE_INTERVAL_S
    data_dir: str = ""
    db_file: str = "cockpit.db"
    log_file: str = "cockpit.log"

    def to_mapping(self) -> dict:
        return {
            "server": {"host": self.server_host, "port": self.server_port},
            "ollama": {"url": self.ollama_url},
            "security": {
                "jwt_secret": self.jwt_secret,
                "session_days": self.session_days,
                "bcrypt_cost": self.bcrypt_cost,
            },
            "telemetry": {
                "nvidia_smi_path": self.nvidia_smi_path,
                "sample_interval_s": self.sample_interval_s,
            },
            "paths": {
                "data_dir": self.data_dir,
                "db_file": self.db_file,
                "log_file": self.log_file,
            },
        }
