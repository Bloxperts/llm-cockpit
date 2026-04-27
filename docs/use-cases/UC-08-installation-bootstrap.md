<!-- Status: Accepted | Version: 1.0 | Created: 2026-04-27 | Updated: 2026-04-27 -->
# UC-08 · Use Case — First-run installation + bootstrap

**Status:** Accepted
**Owner:** Chris
**Functional Spec:** [`functional/UC-08-installation-bootstrap.md`](../specs/functional/UC-08-installation-bootstrap.md)
**Test Spec:** [`test/UC-08-installation-bootstrap.md`](../specs/test/UC-08-installation-bootstrap.md)
**Sprint:** 2
**Depends on:** ADR-002 v1.1 (pip + CLI), ADR-003 (single admin seed, co-located default), UC-07 (Ollama integration is what `init` probes).
**Min role:** N/A — this is the install path, run before any user exists.

## Story

> As a stranger who has Ollama running locally I want to install the cockpit and reach a login screen in five minutes, with no manual configuration files, no SQL, and clear error messages if Ollama isn't reachable, so that the cockpit feels approachable and I can decide if it's useful before investing more time.

> The installer also asks me up front whether the cockpit should be reachable only from this machine (`127.0.0.1`) or from any device on my LAN (`0.0.0.0`), so that I can use the cockpit from a phone or tablet on the same network if I want to.

## Target state

The install path on macOS (Apple Silicon and Intel), Ubuntu LTS, and Debian:

```bash
# Pre-requisite: Ollama is running locally
ollama serve  # or systemctl --user start ollama

# 1. Install the cockpit
pip install llm-cockpit            # or: pipx install llm-cockpit (recommended)

# 2. Bootstrap (interactive but unattended-friendly via env vars)
cockpit-admin init
#  → Probes Ollama at $COCKPIT_OLLAMA_URL (default http://127.0.0.1:11434)
#  → Refuses to proceed if Ollama is unreachable, with a clear "Ollama install guide" link.
#  → Reads $COCKPIT_DATA_DIR (default ~/.local/share/llm-cockpit) and creates it if missing.
#  → Asks: "Bind the cockpit to localhost only (127.0.0.1), or to all interfaces (0.0.0.0)?"
#         Default = localhost. Pick 0.0.0.0 when you want to reach the cockpit from
#         other devices on the LAN (phones, tablets, other laptops). The cockpit
#         prints a reminder: "Cockpit is HTTP only — for off-LAN access use a VPN
#         (Tailscale, WireGuard) or reverse-proxy with TLS."
#  → Writes default config to $COCKPIT_DATA_DIR/config.toml (host = chosen value).
#  → Creates SQLite database at $COCKPIT_DATA_DIR/cockpit.db, runs migrations.
#  → Seeds one user: admin / "ollama" / role=admin / must_change_password=true.
#  → Tags discovered Ollama models per ADR-004 §3 heuristic; persists to model_tags table.
#  → Prints next-step hint: "Run `cockpit-admin serve` to start the cockpit."

# 3. Run
cockpit-admin serve                # foreground; or systemd / Docker for production

# 4. Open http://localhost:8080
# 5. Log in as admin / ollama → forced password change (UC-09) → use.
```

Optional shapes for the same bootstrap:

- **Docker Compose.** `docker-compose up -d` from a published `compose.yml`. Same auto-detect logic in the entrypoint script. SQLite lives in a named volume.
- **systemd-user unit** (Linux). `cockpit-admin systemd-install` writes `~/.config/systemd/user/llm-cockpit.service`, runs `systemctl --user daemon-reload`, prints how to enable + start.

Default `config.toml` (created by `init`):

```toml
[server]
host = "127.0.0.1"
port = 8080

[ollama]
url = "http://127.0.0.1:11434"

[security]
jwt_secret = "<generated, 48 random bytes b64>"
session_days = 7
bcrypt_cost = 12

[telemetry]
nvidia_smi_path = ""    # auto-detected; set to disable
sample_interval_s = 5

[paths]
data_dir = "/home/<user>/.local/share/llm-cockpit"
db_file  = "cockpit.db"
log_file = "cockpit.log"
```

The `init` step is **idempotent**: re-running it on an existing data dir prints what would change, asks for confirmation per change, and exits non-zero if the existing config is incompatible (e.g. SQLite schema is from a newer cockpit version).

## Acceptance criteria

1. On a clean macOS box with `brew install ollama` already done: `pip install llm-cockpit && cockpit-admin init && cockpit-admin serve` lands at `http://localhost:8080/login` in under five minutes (network permitting).
2. On a clean Ubuntu LTS box with `curl -fsSL https://ollama.com/install.sh | sh` already done: same as (1).
3. If Ollama is **not** running, `cockpit-admin init` exits non-zero within 5 s with: "Cannot reach Ollama at http://127.0.0.1:11434. Is `ollama serve` running? See https://ollama.com/download".
4. If `init` is re-run on an existing data dir, it does **not** overwrite the admin user, does **not** wipe the database, and exits zero with "Cockpit is already initialised at &lt;dir&gt;".
5. The seeded admin user (`admin` / `ollama`) is created with `must_change_password=true`. (Cross-validated by UC-09.)
6. The discovered models in Ollama are tagged per ADR-004 §3 heuristic and the result is visible in `model_tags` after `init`.
7. `cockpit-admin --version` prints the package version.
8. `cockpit-admin serve --help` prints the supported flags (host, port, log-level, config path).
9. The bootstrap **never** asks the user for sudo / root.
10. The cockpit's `pip` package contains the built Next.js frontend; `cockpit-admin serve` does not require Node to be installed.
11. `init` asks for the bind interface; defaults to `127.0.0.1` and accepts `0.0.0.0` for LAN access. The chosen value is written to `config.toml` and a reminder is printed about HTTPS being out of scope (use a VPN or reverse proxy for off-LAN access).
12. Re-running `init` with `COCKPIT_NONINTERACTIVE=1` does not prompt for the bind interface; it uses the value from `--bind` flag, then `COCKPIT_HOST` env, then existing config, then `127.0.0.1`.

## Scope boundaries (out)

- Multi-host install. Out — one cockpit, one host, one Ollama.
- Encrypted-at-rest SQLite. Out (file is in a per-user data dir).
- Database migration *down* (rollback) tooling. Up-only migrations in v0.1.
- Bundled `apt` / `brew` / `winget` packages. Out — pip is the v0.1 path; Docker is the convenience path.
- Bundling Ollama itself. Out — Ollama is the user's responsibility.
- TLS / HTTPS. Out — the cockpit serves HTTP on the LAN; reverse-proxy is the operator's job.

## Notes

- The five-minute Mac + Ubuntu walk-through is the v0.1 marketing target. The `README.md` Quick Start mirrors this story exactly.
- `pipx install` is the recommended path for end users; `pip install` works inside a venv. Both are tested.
- The Bloxperts deployment uses `INITIAL_USERS` env var (when implemented in v0.2) to seed extra accounts. v0.1 only seeds `admin`; everyone else is created via UC-06.
