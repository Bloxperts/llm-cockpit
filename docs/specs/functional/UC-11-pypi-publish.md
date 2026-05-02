<!-- Status: Done | Version: 0.3 | Created: 2026-04-29 | Updated: 2026-05-02 -->
# UC-11 - Functional Spec - Public PyPI publishing

**Status:** Done
**User Spec:** [`../../use-cases/UC-11-pypi-publish.md`](../../use-cases/UC-11-pypi-publish.md)
**Test Spec:** [`../test/UC-11-pypi-publish.md`](../test/UC-11-pypi-publish.md)
**Depends on:** ADR-002 v1.1, UC-08, Sprint 10 `v0.4.0`.
**Bound DG:** none. This does not add a runtime platform boundary; it adds release automation around the existing package.

## Goal

Make `llm-cockpit` publishable and installable from PyPI with a repeatable tag-based release process. Sprint 11 should close the gap between "wheel can be built locally" and "a stranger can install the current release without cloning the repo."

## Version target

Target release: `v1.0.0`.

Rationale: Sprint 11 completed the UI/release hardening needed for the first
public PyPI release. `v0.5.7` remains as a beta/history artifact; `v1.0.0` is
the release to advertise.

## Required changes

### Package metadata

Update `pyproject.toml` so packaging checks pass cleanly:

- use SPDX license expression (`license = "MIT"`) and add `license-files` if needed;
- add `project.urls` for Homepage, Repository, Issues, Changelog, and Documentation;
- confirm Python version classifiers match `requires-python >=3.12`;
- confirm package data includes `frontend_dist`, migrations, and default config files;
- use `Development Status :: 5 - Production/Stable` for `v1.0.0`.

### Repository release files

Ensure the repo contains:

- `LICENSE` with the selected license text;
- current `README.md` aligned with the real app state and quick start;
- `CHANGELOG.md` entry for `v0.5.0`;
- no stale docs references such as `docs/STATUS.md` if the file does not exist.

### Build behavior

Create or update a local build command that:

1. installs frontend dependencies from `frontend/package-lock.json` with `npm ci`;
2. builds the Next.js static frontend;
3. syncs/copies the built frontend into `src/cockpit/frontend_dist`;
4. builds wheel and sdist;
5. runs `twine check dist/*`.

The build must start from a clean `build/` and `dist/` directory to avoid stale frontend assets.

### GitHub Actions

Add workflows:

- CI workflow for PRs and pushes to `develop` / `main`:
  - Python 3.12;
  - Node 20;
  - frontend build;
  - backend tests;
  - package build + `twine check`.
- Release workflow for tags `v*.*.*`:
  - build wheel + sdist;
  - upload artifacts;
  - publish to PyPI via trusted publishing (`id-token: write`);
  - optionally support TestPyPI with manual dispatch before production.

### PyPI setup

Document the one-time PyPI setup:

- create/claim the `llm-cockpit` project name;
- configure GitHub trusted publisher for `Bloxperts/llm-cockpit`;
- set the workflow name/environment expected by PyPI;
- record whether the first release uses TestPyPI first.

Do not store PyPI API tokens in the repo.

### Current 2026-05-02 readiness snapshot

Done in the public release block:

- package metadata has SPDX-style license, public URLs, stable classifier, and `LICENSE`;
- frontend build script uses `npm ci` when dependencies need installation;
- CI, TestPyPI, and production tag release workflow skeletons exist;
- GitHub Actions environments `testpypi` and `pypi` exist;
- dashboard/admin model UI is split so the release UI no longer depends on dense cards.

Trusted Publisher configuration to create in PyPI/TestPyPI:

| Index | Project | Owner | Repository | Workflow | Environment |
|---|---|---|---|---|---|
| TestPyPI | `llm-cockpit` | `Bloxperts` | `llm-cockpit` | `testpypi.yml` | `testpypi` |
| PyPI | `llm-cockpit` | `Bloxperts` | `llm-cockpit` | `release.yml` | `pypi` |

If the project does not exist yet, create a **pending publisher**. This does
not reserve the name until the first successful publish.

Release evidence before advertising `v1.0.0` on PyPI:

- clean build refreshes `src/cockpit/frontend_dist`;
- `python -m build` and `twine check dist/*` pass in CI/release workflows;
- isolated local-wheel install smoke passed for the hardening build;
- Neuroforge `cockpit-admin doctor` smoke passed after PyPI install;
- TestPyPI trusted-publisher publish passed;
- Chris explicitly said "go publish PyPI".

### UC-08 Slice E reconcile

As part of Sprint 11, verify UC-08 against the actual shipped code:

- `cockpit-admin init` non-interactive path;
- bind interface behavior;
- idempotent re-run behavior;
- systemd-user unit generation;
- wheel contains frontend assets;
- no sudo requirement.

If a UC-08 acceptance criterion is not true, either fix it in the sprint if it is packaging/install-path scoped, or record a separate backlog item if it is outside Sprint 11.

## Acceptance criteria

1. `python -m build` or the repo-standard equivalent creates wheel and sdist from a clean checkout.
2. `twine check dist/*` passes with no errors.
3. `pipx install --force dist/llm_cockpit-1.0.0-py3-none-any.whl` works on Neuroforge.
4. `cockpit-admin --version` reports `1.0.0`.
5. `cockpit-admin doctor --data-dir /home/bloxperts/.local/share/llm-cockpit --ollama-url http://127.0.0.1:11434` passes on Neuroforge.
6. GitHub release workflow is present and documented for PyPI trusted publishing.
7. TestPyPI/dry-run publication path is documented and either exercised or explicitly blocked with reason.
8. Production PyPI publish is performed only after Chris says "go publish PyPI".
9. Vault and `/docs` are synced at sprint close.

## Risks

- PyPI project-name availability may block production publishing.
- PyPI trusted publisher setup may require browser/account steps by Chris.
- GitHub Actions availability/permissions may require repo owner settings outside the codebase.
- The local macOS wheel build can accidentally include stale frontend assets unless `build/` and `dist/` are cleaned first.
