"""`cockpit-admin init` orchestration.

Resolves the data dir + Ollama URL + bind interface, probes Ollama once
through the `LLMChat` port (UC-07), writes `config.toml`, runs migrations,
seeds the admin user, and snapshots discovered models into `model_tags`.

DI seam: `run_init` and `probe_ollama` accept a `chat_factory` callable that
returns an `LLMChat`-conforming object. The default factory builds
`OllamaLLMChat`; tests inject `FakeLLMChat`. This is the line that satisfies
UC-07 AC-1 ("no direct OLLAMA_URL / httpx calls outside the adapter").
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import tomli_w
from jinja2 import Template

from cockpit.config import (
    DEFAULT_BCRYPT_COST,
    DEFAULT_HOST,
    DEFAULT_OLLAMA_URL,
    DEFAULT_PORT,
    DEFAULT_SAMPLE_INTERVAL_S,
    DEFAULT_SESSION_DAYS,
    TomlConfig,
    default_data_dir,
)
from cockpit.db import (
    ensure_data_dir,
    head_revision,
    make_engine,
    make_session_factory,
    session_scope,
    upgrade_to_head,
)
from cockpit.ports.llm_chat import (
    LLMChat,
    OllamaResponseError,
    OllamaUnreachableError,
)
from cockpit.services.model_tags import load_heuristic, snapshot_tags
from cockpit.services.users import (
    DEFAULT_ADMIN_PASSWORD,
    admin_exists,
    seed_admin,
)

ChatFactory = Callable[[str], LLMChat]


def _default_chat_factory(url: str) -> LLMChat:
    """Production factory: build the real Ollama adapter.

    Imported lazily so `services/bootstrap.py` doesn't pull `httpx` into its
    import graph at module-load time (the adapter does, of course; that's
    where the boundary belongs).
    """
    from cockpit.adapters.ollama_chat import OllamaLLMChat

    return OllamaLLMChat(url)

VALID_BIND_CHOICES = {"127.0.0.1", "0.0.0.0"}
TLS_REMINDER = (
    "Note: cockpit serves HTTP only. For off-LAN / public exposure use a VPN "
    "(Tailscale / WireGuard) or a TLS reverse proxy. v0.1 does not include "
    "built-in TLS."
)


class BootstrapError(Exception):
    """Bootstrap halted before completing. Carries an exit code."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class InitOptions:
    data_dir: Path | None = None
    ollama_url: str | None = None
    admin_password: str | None = None
    bind: str | None = None
    non_interactive: bool = False


@dataclass
class InitResult:
    data_dir: Path
    config_path: Path
    db_path: Path
    bind_host: str
    ollama_url: str
    discovered_models: list[str]
    tagged: dict[str, str]
    already_initialised: bool


def _resolve_data_dir(opt: Path | None) -> Path:
    if opt is not None:
        return opt.expanduser().resolve()
    return default_data_dir().expanduser().resolve()


def _resolve_ollama_url(opt: str | None) -> str:
    return (
        opt
        or os.environ.get("COCKPIT_OLLAMA_URL")
        or os.environ.get("OLLAMA_HOST")
        or DEFAULT_OLLAMA_URL
    )


def probe_ollama(
    url: str,
    *,
    chat_factory: ChatFactory | None = None,
) -> list[str]:
    """Probe Ollama for the discovered model names via the `LLMChat` port.

    Builds a transient adapter (or uses the test factory's), runs
    `list_models()`, and returns the names. Maps port-level failures to
    `BootstrapError(exit_code=1)` with the user-facing install-guide hint.
    """
    factory = chat_factory or _default_chat_factory
    chat = factory(url)

    async def _run() -> list[str]:
        try:
            return [m.name for m in await chat.list_models()]
        finally:
            aclose = getattr(chat, "aclose", None)
            if aclose is not None:
                await aclose()

    try:
        return asyncio.run(_run())
    except OllamaUnreachableError as exc:
        raise BootstrapError(
            f"Cannot reach Ollama at {url}. Is `ollama serve` running? "
            f"See https://ollama.com/download\n  (cause: {exc!s})"
        ) from exc
    except OllamaResponseError as exc:
        raise BootstrapError(
            f"Cannot reach Ollama at {url}. (HTTP {exc.status}: {exc.body[:200]})"
        ) from exc


def _resolve_bind(
    opt: InitOptions,
    existing_host: str | None,
    *,
    stdin=None,
    stdout=None,
) -> str:
    """Bind-interface resolution per UC-08 AC-12.

    Order: --bind > COCKPIT_HOST env > existing config > interactive prompt
    (when allowed) > default 127.0.0.1.
    """
    if stdin is None:
        stdin = sys.stdin
    if stdout is None:
        stdout = sys.stdout
    if opt.bind:
        return _validate_bind(opt.bind)
    env_host = os.environ.get("COCKPIT_HOST")
    if env_host:
        return _validate_bind(env_host)
    if existing_host:
        return _validate_bind(existing_host)
    if opt.non_interactive or os.environ.get("COCKPIT_NONINTERACTIVE") == "1":
        return DEFAULT_HOST
    return _prompt_bind(stdin, stdout)


def _validate_bind(value: str) -> str:
    """Accept `127.0.0.1`, `0.0.0.0`, or any explicit IPv4/IPv6 the user
    passes via `--bind <ip>`. We don't sanity-check arbitrary IPs — the
    operator owns that choice.
    """
    return value.strip()


def _prompt_bind(stdin, stdout) -> str:
    stdout.write(
        "Bind the cockpit to:\n"
        "  [1] localhost only (127.0.0.1)        — only this machine can reach it (default)\n"
        "  [2] all interfaces (0.0.0.0)          — any device on this LAN can reach it\n"
        "Choice [1]: "
    )
    stdout.flush()
    line = stdin.readline().strip()
    if line in ("", "1"):
        return "127.0.0.1"
    if line == "2":
        stdout.write(TLS_REMINDER + "\n")
        stdout.flush()
        return "0.0.0.0"
    # any other input is treated as an explicit address
    return line


def _render_config_toml(toml_cfg: TomlConfig) -> str:
    template_text = resources.files("cockpit").joinpath(
        "default_config/config.toml.j2"
    ).read_text(encoding="utf-8")
    return Template(template_text).render(**toml_cfg.__dict__)


def write_config_toml(path: Path, toml_cfg: TomlConfig) -> None:
    """Write `config.toml` from the Jinja template. We also re-parse + dump
    via tomli_w to validate the output is valid TOML.
    """
    rendered = _render_config_toml(toml_cfg)
    path.write_text(rendered, encoding="utf-8")
    # Sanity round-trip: ensure tomllib can parse it.
    import tomllib

    with path.open("rb") as f:
        parsed = tomllib.load(f)
    # Re-emit canonicalised — guards against template typos producing junk.
    path.write_bytes(tomli_w.dumps(parsed).encode("utf-8"))


def _is_already_initialised(data_dir: Path, db_url: str) -> bool:
    db_path = data_dir / "cockpit.db"
    config_path = data_dir / "config.toml"
    if not (db_path.exists() and config_path.exists()):
        return False
    from cockpit.db import current_revision

    try:
        current = current_revision(db_url)
    except Exception:
        return False
    return current is not None and current == head_revision()


def run_init(
    opt: InitOptions,
    *,
    stdin=None,
    stdout=None,
    chat_factory: ChatFactory | None = None,
) -> InitResult:
    """Run the full `init` flow. Returns a structured result; raises
    `BootstrapError` (with non-zero exit code) on any halting condition.

    `chat_factory` is the DI seam for tests. Defaults to building an
    `OllamaLLMChat` against the resolved URL.
    """
    if stdin is None:
        stdin = sys.stdin
    if stdout is None:
        stdout = sys.stdout
    data_dir = _resolve_data_dir(opt.data_dir)
    ollama_url = _resolve_ollama_url(opt.ollama_url)

    # Step 1-2: data dir.
    ensure_data_dir(data_dir)

    # Step 3-4: probe Ollama via the LLMChat port.
    discovered = probe_ollama(ollama_url, chat_factory=chat_factory)

    # Idempotency check: if DB exists with current schema and config exists,
    # we're done — don't overwrite anything.
    db_url = f"sqlite:///{data_dir / 'cockpit.db'}"
    config_path = data_dir / "config.toml"
    db_path = data_dir / "cockpit.db"

    if _is_already_initialised(data_dir, db_url):
        stdout.write(f"Cockpit is already initialised at {data_dir}\n")
        stdout.flush()
        engine = make_engine(db_url)
        factory = make_session_factory(engine)
        try:
            with session_scope(factory) as session:
                patterns = load_heuristic()
                tagged = snapshot_tags(session, discovered, patterns)
        finally:
            engine.dispose()
        # Pick existing host out of config.toml for the result.
        import tomllib

        with config_path.open("rb") as f:
            existing = tomllib.load(f)
        existing_host = existing.get("server", {}).get("host", DEFAULT_HOST)
        return InitResult(
            data_dir=data_dir,
            config_path=config_path,
            db_path=db_path,
            bind_host=existing_host,
            ollama_url=ollama_url,
            discovered_models=discovered,
            tagged=tagged,
            already_initialised=True,
        )

    # Step 5: bind interface.
    existing_host: str | None = None
    if config_path.exists():
        import tomllib

        with config_path.open("rb") as f:
            existing = tomllib.load(f)
        existing_host = existing.get("server", {}).get("host")
    bind_host = _resolve_bind(opt, existing_host, stdin=stdin, stdout=stdout)

    # Step 7 (a): write config.toml.
    toml_cfg = TomlConfig(
        server_host=bind_host,
        server_port=DEFAULT_PORT,
        ollama_url=ollama_url,
        jwt_secret=secrets.token_urlsafe(48),
        session_days=DEFAULT_SESSION_DAYS,
        bcrypt_cost=DEFAULT_BCRYPT_COST,
        nvidia_smi_path="",
        sample_interval_s=DEFAULT_SAMPLE_INTERVAL_S,
        data_dir=str(data_dir),
        db_file="cockpit.db",
        log_file="cockpit.log",
    )
    write_config_toml(config_path, toml_cfg)

    # Step 7 (b): run migrations.
    upgrade_to_head(db_url)

    # Step 8: seed admin.
    admin_password = (
        opt.admin_password
        or os.environ.get("COCKPIT_ADMIN_PASSWORD")
        or DEFAULT_ADMIN_PASSWORD
    )
    if admin_password == DEFAULT_ADMIN_PASSWORD:
        stdout.write(
            "WARNING: seeded admin password is the literal default 'ollama'. "
            "You will be required to change it on first login (UC-09).\n"
        )

    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    try:
        with session_scope(factory) as session:
            if not admin_exists(session):
                seed_admin(session, password=admin_password, bcrypt_cost=DEFAULT_BCRYPT_COST)

            # Step 9: snapshot model tags.
            patterns = load_heuristic()
            tagged = snapshot_tags(session, discovered, patterns)
    finally:
        engine.dispose()

    return InitResult(
        data_dir=data_dir,
        config_path=config_path,
        db_path=db_path,
        bind_host=bind_host,
        ollama_url=ollama_url,
        discovered_models=discovered,
        tagged=tagged,
        already_initialised=False,
    )
