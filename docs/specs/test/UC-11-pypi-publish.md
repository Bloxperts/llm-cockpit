<!-- Status: Review | Version: 0.2 | Created: 2026-04-29 | Updated: 2026-05-02 -->
# UC-11 - Test Spec - Public PyPI publishing

**Status:** Review
**User Spec:** [`../../use-cases/UC-11-pypi-publish.md`](../../use-cases/UC-11-pypi-publish.md)
**Functional Spec:** [`../functional/UC-11-pypi-publish.md`](../functional/UC-11-pypi-publish.md)

## Approach

This is a release-engineering user story. Tests are mostly build, metadata, install, and smoke checks. Runtime behavior should be covered by the existing suite; Sprint 11 adds confidence that the same runtime ships correctly as a public package.

## Automated test cases

| ID | Maps to AC | Description | Method |
|----|------------|-------------|--------|
| T-01 | AC-1, AC-2 | Build from a clean checkout after removing `build/` and `dist/`; wheel and sdist are produced. | `python -m build` or repo-standard equivalent |
| T-02 | AC-2 | Package metadata passes validation. | `twine check dist/*` |
| T-03 | AC-1 | Wheel contents include `cockpit/frontend_dist/index.html`, migrations, and default config files. | archive inspection |
| T-04 | AC-4 | `cockpit-admin --version` reports the target version after wheel install in an isolated environment. | subprocess |
| T-05 | AC-6 | GitHub Actions workflow YAML is valid enough for `gh workflow list` / `actionlint` if available. | CI/lint |
| T-06 | UC-08 | `cockpit-admin init --non-interactive --data-dir tmp --admin-password ...` succeeds against fake Ollama. | pytest |
| T-07 | UC-08 | Re-running `init` is idempotent and does not overwrite admin. | pytest |
| T-08 | UC-08 | `systemd-install --help` and generated unit shape include the pipx executable path or documented override. | pytest/subprocess |

## Manual / environment checks

| ID | Description | Expected |
|----|-------------|----------|
| M-01 | Install local wheel on Neuroforge with `pipx install --force dist/llm_cockpit-<version>-py3-none-any.whl`. | `~/.local/bin/cockpit-admin --version` prints the wheel version. For public PyPI final, that version must be `1.0.0`. |
| M-02 | Run `cockpit-admin doctor` on Neuroforge against `/home/bloxperts/.local/share/llm-cockpit` and local Ollama. | All checks OK. |
| M-03 | TestPyPI trusted-publisher path. | Configure a pending publisher on TestPyPI with owner `Bloxperts`, repo `llm-cockpit`, workflow `testpypi.yml`, environment `testpypi`; then run the `TestPyPI` workflow manually. If account/project setup blocks it, record the blocker. |
| M-04 | Production PyPI publish, only after Chris says "go publish PyPI". | `pipx install llm-cockpit` installs the same version as the GitHub release tag. |

## Pass criteria

- All automated cases pass locally or in CI.
- Neuroforge local-wheel pipx smoke passes.
- No production PyPI publish occurs without explicit Chris approval.
- If PyPI account/trusted-publisher setup blocks release, the blocker is documented in `process/SPRINT_STATE.md` with exact next action.
