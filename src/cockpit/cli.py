"""`cockpit-admin` — the cockpit's single CLI surface.

UC-08 Slice A wired `init`, `migrate`, and `doctor`. UC-08 Slice B wires
`serve` against the FastAPI app factory (`cockpit.main:create_app`). The
remaining subcommands (`user-*`, `systemd-install`) stay stubbed so
`--help` is honest and future PRs can fill them in without churn.

Exit codes:
    0  success
    1  user-facing error (Ollama unreachable, validation failure, etc.)
    2  feature not implemented in this slice
"""

from __future__ import annotations

import argparse
import shutil
import sys
from importlib import resources
from pathlib import Path

from cockpit import __version__
from cockpit.config import default_data_dir
from cockpit.db import current_revision, head_revision, upgrade_to_head
from cockpit.services.bootstrap import (
    BootstrapError,
    InitOptions,
    probe_ollama,
    run_init,
)

DEFERRED_EXIT = 2


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cockpit-admin",
        description="Admin CLI for llm-cockpit (init, serve, doctor, user management).",
    )
    p.add_argument("--version", action="version", version=f"cockpit-admin {__version__}")
    sub = p.add_subparsers(dest="cmd", required=False)

    # init
    init = sub.add_parser("init", help="Bootstrap a fresh cockpit data dir.")
    init.add_argument("--data-dir", type=Path, default=None)
    init.add_argument("--ollama-url", type=str, default=None)
    init.add_argument("--admin-password", type=str, default=None)
    init.add_argument("--bind", type=str, default=None,
                      help="Bind interface (127.0.0.1, 0.0.0.0, or any explicit IP).")
    init.add_argument("--non-interactive", action="store_true")
    init.set_defaults(func=cmd_init)

    # serve
    serve = sub.add_parser("serve", help="Run the cockpit FastAPI app.")
    serve.add_argument("--host", type=str, default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--config", type=Path, default=None,
                       help="Path to config.toml (default: <data-dir>/config.toml).")
    serve.add_argument("--data-dir", type=Path, default=None,
                       help="Data dir, used if --config is omitted.")
    serve.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                       default="INFO")
    serve.set_defaults(func=cmd_serve)

    # migrate
    mig = sub.add_parser("migrate", help="Run alembic upgrade head against the data-dir DB.")
    mig.add_argument("--data-dir", type=Path, default=None)
    mig.set_defaults(func=cmd_migrate)

    # doctor
    doc = sub.add_parser("doctor", help="Diagnose Ollama / DB / data-dir / frontend / nvidia-smi.")
    doc.add_argument("--data-dir", type=Path, default=None)
    doc.add_argument("--ollama-url", type=str, default=None)
    doc.set_defaults(func=cmd_doctor)

    # user-* and systemd-install — stubs.
    for stub_name, help_text in (
        ("user-add", "Add a user (UC-06)."),
        ("user-list", "List users (UC-06)."),
        ("user-set-password", "Reset a user's password (UC-06)."),
        ("user-set-role", "Change a user's role (UC-06)."),
        ("user-delete", "Delete a user (UC-06)."),
        ("systemd-install", "Install systemd-user unit (Slice B)."),
    ):
        s = sub.add_parser(stub_name, help=help_text)
        s.set_defaults(func=cmd_deferred_stub, stub_name=stub_name)

    return p


def cmd_init(args: argparse.Namespace) -> int:
    opt = InitOptions(
        data_dir=args.data_dir,
        ollama_url=args.ollama_url,
        admin_password=args.admin_password,
        bind=args.bind,
        non_interactive=args.non_interactive,
    )
    try:
        result = run_init(opt)
    except BootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code

    if result.already_initialised:
        return 0

    print(f"Bootstrap complete. Data dir: {result.data_dir}")
    print(f"  config: {result.config_path}")
    print(f"  db:     {result.db_path}")
    print(f"  bind:   {result.bind_host}")
    print(f"  ollama: {result.ollama_url} ({len(result.discovered_models)} models discovered)")
    if result.tagged:
        for name, tag in sorted(result.tagged.items()):
            print(f"    tagged {name:40s} → {tag}")
    print("\nRun `cockpit-admin serve` to start the cockpit.")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the cockpit FastAPI app via uvicorn.

    Resolution order for settings:
        1. config.toml at --config or <data-dir>/config.toml (if it exists).
        2. COCKPIT_* env vars.
        3. CLI flags --host / --port (override 1 + 2 when set).
        4. Defaults baked into `cockpit.config.Settings`.
    """
    from cockpit.config import Settings
    from cockpit.main import create_app

    data_dir = (args.data_dir or default_data_dir()).expanduser().resolve()
    config_path = (args.config or (data_dir / "config.toml")).expanduser().resolve()

    if config_path.exists():
        settings = Settings.from_toml(config_path)
    else:
        print(
            f"No config.toml at {config_path}; falling back to env / defaults. "
            f"Run `cockpit-admin init` for a proper bootstrap.",
            file=sys.stderr,
        )
        settings = Settings()

    if args.host is not None:
        settings = settings.model_copy(update={"host": args.host})
    if args.port is not None:
        settings = settings.model_copy(update={"port": args.port})

    app = create_app(settings)

    import uvicorn

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=args.log_level.lower(),
    )
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    data_dir = (args.data_dir or default_data_dir()).expanduser().resolve()
    db_path = data_dir / "cockpit.db"
    db_url = f"sqlite:///{db_path}"
    if not db_path.exists():
        # Alembic will create it; informational only.
        print(f"DB not present yet; alembic will create {db_path}", file=sys.stderr)
    upgrade_to_head(db_url)
    print(f"Migrated {db_path} → head ({head_revision()}).")
    return 0


def _check_data_dir_writable(data_dir: Path) -> tuple[bool, str]:
    if not data_dir.exists():
        return False, f"data dir does not exist: {data_dir}"
    if not data_dir.is_dir():
        return False, f"data dir is not a directory: {data_dir}"
    probe = data_dir / ".cockpit-doctor-probe"
    try:
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return False, f"data dir not writable: {data_dir} ({exc})"
    return True, f"data dir writable: {data_dir}"


def _check_db_schema(data_dir: Path) -> tuple[bool, str]:
    db_path = data_dir / "cockpit.db"
    if not db_path.exists():
        return False, f"db missing: {db_path}"
    db_url = f"sqlite:///{db_path}"
    try:
        cur = current_revision(db_url)
    except Exception as exc:
        return False, f"db schema unreadable: {exc}"
    head = head_revision()
    if cur is None:
        return False, "db is empty (no alembic_version row)"
    if cur != head:
        return False, f"db schema is {cur}, expected {head}"
    return True, f"db schema is current ({cur})"


def _check_ollama(url: str) -> tuple[bool, str]:
    """Probe Ollama via the `LLMChat` port (UC-07). Hard failure on either
    `OllamaUnreachableError` or `OllamaResponseError`.
    """
    try:
        models = probe_ollama(url)
    except BootstrapError as exc:
        return False, str(exc).splitlines()[0]
    return True, f"ollama reachable at {url} ({len(models)} models)"


def _check_frontend_assets() -> tuple[bool, str]:
    try:
        index = resources.files("cockpit").joinpath("frontend_dist/index.html")
        if index.is_file():
            return True, "frontend assets present"
    except Exception:
        pass
    return False, "frontend assets missing — wheel build did not bundle frontend_dist/"


def _check_nvidia_smi() -> tuple[bool, str]:
    path = shutil.which("nvidia-smi")
    if path:
        return True, f"nvidia-smi detected at {path}"
    return False, "nvidia-smi not on PATH (telemetry will report empty state)"


def cmd_doctor(args: argparse.Namespace) -> int:
    data_dir = (args.data_dir or default_data_dir()).expanduser().resolve()
    ollama_url = args.ollama_url or _ollama_url_from_config(data_dir)

    checks = [
        ("ollama_reachable",   _check_ollama(ollama_url)),
        ("db_schema_current",  _check_db_schema(data_dir)),
        ("data_dir_writable",  _check_data_dir_writable(data_dir)),
        ("frontend_assets",    _check_frontend_assets()),
        ("nvidia_smi",         _check_nvidia_smi()),
    ]

    # Hard failures: ollama, db, data_dir, frontend_assets (graduated in
    # Slice B once we ship the placeholder HTML). nvidia_smi stays warn-only
    # because GPU hardware is optional per ADR-003 §5.
    hard_failures = 0
    hard_check_names = {
        "ollama_reachable",
        "db_schema_current",
        "data_dir_writable",
        "frontend_assets",
    }
    for name, (ok, message) in checks:
        marker = "OK  " if ok else "FAIL" if name in hard_check_names else "WARN"
        print(f"[{marker}] {name}: {message}")
        if not ok and marker == "FAIL":
            hard_failures += 1
    return 0 if hard_failures == 0 else 1


def _ollama_url_from_config(data_dir: Path) -> str:
    """Read `[ollama] url` out of `data_dir/config.toml`. Falls back to the
    default if the file or key is missing.
    """
    import tomllib

    from cockpit.config import default_ollama_url

    config_path = data_dir / "config.toml"
    if not config_path.exists():
        return default_ollama_url()
    try:
        with config_path.open("rb") as f:
            cfg = tomllib.load(f)
    except Exception:
        return default_ollama_url()
    return cfg.get("ollama", {}).get("url") or default_ollama_url()


def cmd_deferred_stub(args: argparse.Namespace) -> int:
    name = getattr(args, "stub_name", "<subcommand>")
    print(
        f"`cockpit-admin {name}` is not implemented in this slice. "
        f"See the UC roadmap in docs/process/SPRINT_STATE.md.",
        file=sys.stderr,
    )
    return DEFERRED_EXIT


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
