# llm-cockpit

> Retired local Ollama cockpit and Conductor observer.

**Status:** Retired as an active Bloxperts product on 2026-06-03.

This repository is kept for history only. Do not use it as the active UI, deployment source, or place for new BloxBoard/BloxGuard module work.

## Replacement Boundary

- BloxBoard runtime and module UI frames now live in `Bloxperts/bloxboard-runtime`.
- AgenticBlox platform/runtime/module logic now lives in `Bloxperts/agentic-blox`.
- Cockpit/Conductor is no longer an active deployment surface.
- Cortex is retired and must not be treated as a default node, active endpoint, or routeable runtime.

## What This Repo Contains

The historical Cockpit codebase includes:

- FastAPI app and `cockpit-admin` CLI.
- Auth, users, roles, sessions, and first-login password change flow.
- Ollama dashboard/model telemetry.
- Chat and code routes backed by Ollama.
- Admin surfaces for users, Ollama configuration, and audit.
- Code workspace file handling.
- Bundled static frontend assets.
- Read-only Conductor observer code.

This code remains useful as reference material, but it should not grow sideways into BloxBoard or BloxGuard.

## Retirement Rules

- No new product work should target this repository.
- No runtime host should consume this repo as a deployment source.
- Existing branches or stashes should be reviewed only for extraction into active repos.
- Conductor is disabled by default in new settings.
- If old Cockpit behavior is needed, create an explicit restoration plan first.

## Historical Quick Start

The original local development shape was:

```bash
uv sync --extra dev
cockpit-admin init
cockpit-admin serve
```

That path is retained for archaeology and tests, not for active deployment.

## Validation

Focused retirement checks:

```bash
PYTHONPATH=src /opt/homebrew/bin/python3 -m pytest tests/test_conductor_snapshot.py -q
PYTHONPATH=src /opt/homebrew/bin/python3 -m pytest tests/test_config_retirement.py -q
```

## License

Historical private/internal Bloxperts code unless otherwise superseded by repository metadata.
