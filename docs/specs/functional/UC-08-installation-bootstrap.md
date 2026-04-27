<!-- Status: Accepted | Version: 1.0 | Created: 2026-04-27 | Updated: 2026-04-27 -->
# UC-08 · Functional Spec — First-run installation + bootstrap

**Status:** Accepted
**Depends on:** ADR-002 v1.1 (pip + CLI), ADR-003 (single admin seed, co-located default), UC-07 (Ollama integration probe).
**User Spec:** [`../../use-cases/UC-08-installation-bootstrap.md`](../../use-cases/UC-08-installation-bootstrap.md)
**Test Spec:** [`../test/UC-08-installation-bootstrap.md`](../test/UC-08-installation-bootstrap.md)
**Bound DG:** none (the install path itself doesn't add a new boundary; it uses `LLMChat` from UC-07 to probe Ollama).

## Goal

A pip-installable cockpit that any reasonably-technical Ollama user can run in five minutes. The `cockpit-admin` CLI is the single bootstrap surface: it auto-detects Ollama, creates a data dir, runs migrations, seeds the admin user, applies the default model-tag heuristic, and writes a `config.toml`. Idempotent on re-run.

## Package layout

```
llm-cockpit/                                  ← Python package on PyPI
├── pyproject.toml
├── src/cockpit/
│   ├── __init__.py
│   ├── cli.py                                ← cockpit-admin entry point
│   ├── main.py                               ← FastAPI app
│   ├── config.py
│   ├── db.py                                 ← SQLAlchemy engine, alembic glue
│   ├── adapters/{ollama_chat.py, telemetry.py, fake_chat.py}
│   ├── ports/{llm_chat.py, telemetry.py}
│   ├── routers/{auth.py, chat.py, code.py, dashboard.py, admin_users.py, admin_ollama.py}
│   ├── services/{users.py, model_tags.py, metrics.py, audit.py}
│   ├── models.py                             ← SQLAlchemy ORM
│   ├── schemas.py                            ← Pydantic
│   ├── migrations/                           ← alembic versions
│   ├── frontend_dist/                        ← built Next.js static assets, bundled at build time
│   └── default_config/
│       ├── model_tag_heuristics.yaml
│       └── code_default_system_prompt.md
└── README.md, LICENSE, CHANGELOG.md
```

`cockpit-admin` is registered as a console script in `pyproject.toml`:

```toml
[project.scripts]
cockpit-admin = "cockpit.cli:main"
```

The Next.js frontend is built at wheel-build time (`npm run build && npm run export` → `out/`) and copied into `src/cockpit/frontend_dist/`. FastAPI serves it via `app.mount("/", StaticFiles(directory=frontend_dist, html=True))` for any path that isn't `/api/*`.

## CLI surface

```
cockpit-admin --version
cockpit-admin --help

cockpit-admin init [--data-dir DIR] [--ollama-url URL] [--admin-password PASS]
                   [--bind 127.0.0.1|0.0.0.0|<ip>] [--non-interactive]
cockpit-admin serve [--host H] [--port P] [--config FILE] [--log-level INFO|DEBUG]
cockpit-admin migrate                 # alembic upgrade head
cockpit-admin user-add ...            # see UC-06
cockpit-admin user-delete ...
cockpit-admin user-set-role ...
cockpit-admin user-set-password ...
cockpit-admin user-list ...
cockpit-admin doctor                  # diagnostics: ollama reachable? db schema current? GPU detected?
cockpit-admin systemd-install         # writes ~/.config/systemd/user/llm-cockpit.service (Linux only)
```

`init` flow:

1. Resolve `data_dir` (env `COCKPIT_DATA_DIR` → `--data-dir` → `$XDG_DATA_HOME/llm-cockpit` → `~/.local/share/llm-cockpit`).
2. Create `data_dir` if missing.
3. Resolve Ollama URL (env `COCKPIT_OLLAMA_URL` → `--ollama-url` → `OLLAMA_HOST` → `http://127.0.0.1:11434`).
4. Probe Ollama: `GET /api/tags`. On failure, exit 1 with the install-guide hint.
5. **Resolve bind interface** (`COCKPIT_HOST` env → `--bind` → existing `config.toml` host → interactive prompt → default `127.0.0.1`). When interactive, prompt:

   ```
   Bind the cockpit to:
     [1] localhost only (127.0.0.1)        — only this machine can reach it (default)
     [2] all interfaces (0.0.0.0)          — any device on this LAN can reach it
   Choice [1]:
   ```

   On `[2]`, print: `Note: cockpit serves HTTP only. For off-LAN / public exposure use a VPN (Tailscale / WireGuard) or a TLS reverse proxy. v0.1 does not include built-in TLS.`

6. If `data_dir/cockpit.db` exists with current schema → print "already initialised", exit 0.
7. Otherwise: write `data_dir/config.toml` (template in `default_config/config.toml.j2`) with the resolved `[server] host`.
7. Run alembic `upgrade head` against `data_dir/cockpit.db`.
8. Seed `admin` user with `must_change_password=true`. Password resolution: `--admin-password` → `COCKPIT_ADMIN_PASSWORD` env → literal `"ollama"` (with a printed warning that it must be changed on first login).
9. Snapshot Ollama's current model list, run the heuristic from `model_tag_heuristics.yaml`, write `model_tags` rows.
10. Print "Bootstrap complete. Run `cockpit-admin serve` to start the cockpit."

`serve` flow:

1. Load `data_dir/config.toml`.
2. Ensure DB schema is current (auto-`upgrade head` on start).
3. Start FastAPI with the resolved settings.
4. On startup probe Ollama once; log warning if unreachable but **do not** exit (let the user log in and see the dashboard's "Ollama unreachable" badge).

## Data dir layout

```
$COCKPIT_DATA_DIR/
├── config.toml
├── cockpit.db                  ← SQLite
├── cockpit.db-shm
├── cockpit.db-wal
└── logs/
    └── cockpit.log              ← JSONL per DP-002
```

## Build and CI

- `pip install --upgrade build && python -m build` builds both sdist and wheel.
- The wheel build is preceded by `npm ci && npm run build && npm run export` in `frontend/`, then `cp -r frontend/out src/cockpit/frontend_dist`.
- CI must have Node 20 + Python 3.12.

## Acceptance criteria

- See User Spec §Acceptance criteria. Test Spec automates each.
- `cockpit-admin --version` prints the package version and exits zero.
- `cockpit-admin doctor` runs five checks (Ollama reachable, DB schema current, data-dir writable, frontend assets present, `nvidia-smi` detected) and exits zero only if all pass.

## Risks / open architecture questions for Sprint 1

- **Frontend bundling shape.** Static export vs Next.js standalone vs server-rendered embed. Static export is simplest (works inside FastAPI's StaticFiles); we lose Server Components for SSR. Decision: **static export.** Server Components are nice-to-have, not load-bearing; keep them as a v0.2 if needed.
- **Alembic vs raw SQL migrations.** Alembic is heavier but standard. v0.1 ships with Alembic so future migrations are sane.
- **Cross-platform paths.** Windows is **not** a v0.1 target for the install path (Mac + Linux only); the cockpit *should* run on Windows under WSL but we don't test that.
