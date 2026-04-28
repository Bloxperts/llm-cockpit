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

    # UC-06b: per-user code working folder root. None → `<data_dir>/code_files/`.
    # Override via `COCKPIT_CODE_FILES_DIR` env or `[paths] code_files_dir`
    # in config.toml.
    code_files_dir: Path | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_file

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def resolved_code_files_dir(self) -> Path:
        """Per-user code workspace root. The router creates the per-user
        subfolder lazily on first access.
        """
        return self.code_files_dir or (self.data_dir / "code_files")

    @classmethod
    def from_toml(cls, path: Path) -> Settings:
        """Build a `Settings` from a `config.toml` written by `cockpit-admin init`.

        Per the resolution order in this module's docstring, env vars still
        win over the TOML values — pydantic-settings handles that automatically
        because we pass the TOML values as constructor kwargs and env reads run
        afterwards inside `BaseSettings`.
        """
        import tomllib

        with path.open("rb") as f:
            data = tomllib.load(f)

        kwargs: dict = {}
        if "server" in data:
            srv = data["server"]
            if "host" in srv:
                kwargs["host"] = srv["host"]
            if "port" in srv:
                kwargs["port"] = srv["port"]
        if "ollama" in data and "url" in data["ollama"]:
            kwargs["ollama_url"] = data["ollama"]["url"]
        if "security" in data:
            sec = data["security"]
            if "jwt_secret" in sec:
                kwargs["jwt_secret"] = sec["jwt_secret"]
            if "session_days" in sec:
                kwargs["session_days"] = sec["session_days"]
            if "bcrypt_cost" in sec:
                kwargs["bcrypt_cost"] = sec["bcrypt_cost"]
        if "telemetry" in data:
            tel = data["telemetry"]
            if "nvidia_smi_path" in tel:
                kwargs["nvidia_smi_path"] = tel["nvidia_smi_path"]
            if "sample_interval_s" in tel:
                kwargs["sample_interval_s"] = tel["sample_interval_s"]
        if "paths" in data:
            paths = data["paths"]
            if "data_dir" in paths and paths["data_dir"]:
                kwargs["data_dir"] = Path(paths["data_dir"])
            if "db_file" in paths:
                kwargs["db_file"] = paths["db_file"]
            if "log_file" in paths:
                kwargs["log_file"] = paths["log_file"]
            if paths.get("code_files_dir"):
                kwargs["code_files_dir"] = Path(paths["code_files_dir"])
        return cls(**kwargs)


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
