<!-- Status: Accepted | Version: 0.1 | Created: 2026-04-27 -->
# UC-08 · Test Spec — First-run installation + bootstrap

**Status:** Accepted
**User Spec:** [`../../use-cases/UC-08-installation-bootstrap.md`](../../use-cases/UC-08-installation-bootstrap.md)
**Functional Spec:** [`../functional/UC-08-installation-bootstrap.md`](../functional/UC-08-installation-bootstrap.md)

## Approach

A mix of CLI tests (subprocess invocations of `cockpit-admin`) and integration tests against an ephemeral data dir. A small `tests/integration/test_e2e_install.sh` runs the full happy-path on the developer's machine and on CI; the canonical assertion is "we land at the login screen in under 30 s of wall time on CI".

## Automated test cases

| ID | Maps to AC | Description | Method |
|----|------------|-------------|--------|
| T-01 | AC-1, AC-2 | `cockpit-admin init --data-dir tmp --non-interactive --admin-password 'PWchange1'` succeeds when a fake Ollama serves `/api/tags` on `127.0.0.1:11434`. | auto (pytest) |
| T-02 | AC-3 | With Ollama unreachable, `init` exits non-zero within 5 s and stderr contains "Cannot reach Ollama". | auto |
| T-03 | AC-4 | Re-running `init` on an existing data dir prints "already initialised", does not overwrite admin's password, exits zero. | auto |
| T-04 | AC-5 | Database after `init` has exactly one user (`admin`, role `admin`, `must_change_password=1`). | auto (sqlite assert) |
| T-05 | AC-6 | After `init` against a fake Ollama with two models (`gemma3:27b`, `qwen3-coder:30b`), `model_tags` has rows tagging them `chat` and `code` respectively. | auto |
| T-06 | AC-7 | `cockpit-admin --version` prints a non-empty version string and exits zero. | auto |
| T-07 | AC-8 | `cockpit-admin serve --help` prints `--host`, `--port`, `--config`, `--log-level`. | auto (substring match) |
| T-08 | AC-9 | `init` does not invoke `sudo` (audited via patched `subprocess.run`). | auto |
| T-09 | AC-10 | The installed wheel contains `frontend_dist/index.html`; `cockpit-admin serve` returns it on `GET /` without Node being present. | auto |
| T-10 | — | `cockpit-admin doctor` exits zero on a healthy install, exits non-zero with diagnostics when Ollama is stopped. | auto |

## Manual smoke

| ID | Description | Expected |
|----|-------------|----------|
| M-01 | `pip install dist/llm-cockpit-*.whl` on a clean macOS box, then full happy-path. | Login screen reachable, &lt; 5 min wall time. |
| M-02 | `pip install` then `cockpit-admin systemd-install` on Ubuntu LTS. | Service unit installed; `systemctl --user start llm-cockpit` works. |
| M-03 | `docker compose up -d` against the published `compose.yml`. | Login screen reachable on `http://localhost:8080`. |

## Pass criteria

- All 10 auto cases pass on `develop` and `main`.
- Manual smokes pass at sprint review of Sprint 2.
- `pytest --cov` ≥ 90 % on `cockpit/cli.py`, `cockpit/services/users.py` (init path), and `cockpit/services/model_tags.py` (heuristic).
