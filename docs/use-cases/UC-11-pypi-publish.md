<!-- Status: Review | Version: 0.1 | Created: 2026-04-29 | Updated: 2026-04-29 -->
# UC-11 - Use Case - Public PyPI publishing

**Status:** Review
**Owner:** Chris
**Functional Spec:** [`functional/UC-11-pypi-publish.md`](../specs/functional/UC-11-pypi-publish.md)
**Test Spec:** [`test/UC-11-pypi-publish.md`](../specs/test/UC-11-pypi-publish.md)
**Sprint:** 12
**Depends on:** ADR-002 v1.1, UC-08, Sprint 10 `v0.4.0`.
**Min role:** N/A - release engineering.

## Story

> As a local Ollama user who is not inside Bloxperts, I want to install LLM Cockpit `1.0.0` from PyPI with `pipx install llm-cockpit`, see trustworthy package metadata and documentation, and know which release I am running, so that trying the cockpit does not require cloning the GitHub repository.

> As the project maintainer, I want releases to be reproducible from GitHub tags and published through PyPI trusted publishing, so that a release is not a fragile manual upload from one laptop.

## Target state

The public install path is:

```bash
pipx install llm-cockpit
cockpit-admin --version
cockpit-admin init
cockpit-admin serve
```

The package page on PyPI shows:

- current README with working quick start;
- project URLs for homepage, repository, issues, changelog, and documentation;
- license metadata that passes modern packaging checks;
- wheel and sdist artifacts for the same version as the GitHub release tag.

GitHub has a release workflow that:

1. runs on SemVer tags `vX.Y.Z`;
2. builds the Next.js frontend bundle;
3. builds Python wheel and sdist;
4. checks metadata (`twine check`);
5. publishes to PyPI through trusted publishing / OIDC;
6. stores artifacts on the GitHub release.

## Acceptance criteria

1. `pyproject.toml` contains complete public package metadata: license expression, URLs, supported Python versions, classifiers, and included package data.
2. `README.md` and the vault README no longer describe the project as "design phase"; they describe the latest released install path.
3. A release workflow exists for tag-triggered builds and PyPI trusted publishing.
4. A package build from a clean checkout produces both `.whl` and `.tar.gz`.
5. `twine check dist/*` passes.
6. `pipx install dist/llm_cockpit-<version>-py3-none-any.whl` works on Neuroforge and exposes `cockpit-admin`.
7. `cockpit-admin doctor` passes on Neuroforge against the existing data dir and Ollama.
8. A TestPyPI or dry-run publication path is documented before first production PyPI publication.
9. The first production PyPI release is tagged consistently with repo SemVer and the GitHub release.
10. No runtime behavior change is required beyond packaging, docs, and release automation unless UC-08 Slice E verification finds a blocking install bug.

## Scope boundaries

Out:

- Docker publishing.
- Homebrew / apt / winget packages.
- TLS, reverse proxy, or public hosting.
- Installing Ollama.
- Reworking the app UI.

## Notes

- This is the backlog item previously called "PyPI publish sprint". It was moved from Sprint 11 to Sprint 12 after Chris decided that the UI and remaining functionality should be live before the public `1.0.0` PyPI release.
- If PyPI project-name ownership or account setup blocks automation, Sprint 11 may close with TestPyPI + documented manual production steps, but the blocker must be recorded in `SPRINT_STATE.md`.
