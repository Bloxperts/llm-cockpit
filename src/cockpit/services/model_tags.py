"""Model-name → tag heuristic per ADR-004 §3.

Public API:
    load_heuristic(path=None)       → list[re.Pattern]   # compiled patterns
    load_heuristic_from_yaml(text)  → list[re.Pattern]   # parse arbitrary YAML
    tag_for(name, patterns)         → 'chat' | 'code'
    snapshot_tags(session, model_names, patterns)  → dict[str, str]
    reapply_heuristics(session, available_models, yaml_override=None) → None

`snapshot_tags` is the original UC-02 helper — insert-only, called once
on bootstrap to seed `model_tags` with auto rows.

`reapply_heuristics` is the UC-10 helper — walks the available-model
list, recomputes the auto tag for every row whose `source='auto'`, and
inserts a fresh `source='auto'` row for any model that doesn't have a
row yet. Override rows are untouched.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from cockpit.models import ModelTag, Setting

SETTINGS_KEY_TAG_HEURISTICS = "tag_heuristics_yaml"


def load_heuristic(path: Path | None = None) -> list[re.Pattern[str]]:
    """Load and compile the regex list. If `path` is None, read the
    bundled `default_config/model_tag_heuristics.yaml`.
    """
    if path is None:
        text = resources.files("cockpit").joinpath(
            "default_config/model_tag_heuristics.yaml"
        ).read_text(encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    return load_heuristic_from_yaml(text)


def load_heuristic_from_yaml(text: str) -> list[re.Pattern[str]]:
    """Parse a YAML body and return the compiled patterns. Used by
    UC-10's PUT settings handler so the admin can edit the YAML in the
    UI and have it take effect without a restart.

    Raises `yaml.YAMLError` if the body is malformed; the caller maps
    that to a 400.
    """
    data = yaml.safe_load(text) or {}
    raw_patterns = data.get("code_patterns", []) or []
    return [re.compile(p, re.IGNORECASE) for p in raw_patterns]


def tag_for(model_name: str, patterns: list[re.Pattern[str]]) -> str:
    """Return `'code'` if any pattern matches the name, else `'chat'`."""
    for p in patterns:
        if p.search(model_name):
            return "code"
    return "chat"


def snapshot_tags(
    session: Session,
    model_names: list[str],
    patterns: list[re.Pattern[str]],
) -> dict[str, str]:
    """Apply the heuristic to every name and persist (insert-only) into
    `model_tags`. Existing rows — including admin overrides — are left alone.

    Returns a `{model: tag}` dict for what was just decided (whether new or
    pre-existing).
    """
    out: dict[str, str] = {}
    for name in model_names:
        existing = session.query(ModelTag).filter_by(model=name).first()
        if existing is not None:
            out[name] = existing.tag
            continue
        decided = tag_for(name, patterns)
        session.add(ModelTag(model=name, tag=decided, source="auto"))
        out[name] = decided
    session.flush()
    return out


def _resolve_patterns(
    session: Session, yaml_override: str | None
) -> list[re.Pattern[str]]:
    """Pick the right pattern source: explicit YAML > persisted setting >
    bundled default."""
    if yaml_override is not None:
        return load_heuristic_from_yaml(yaml_override)

    row = session.execute(
        select(Setting).where(Setting.key == SETTINGS_KEY_TAG_HEURISTICS)
    ).scalar_one_or_none()
    if row is not None and row.value:
        return load_heuristic_from_yaml(row.value)
    return load_heuristic()


def reapply_heuristics(
    session: Session,
    available_models: list[str],
    yaml_override: str | None = None,
) -> dict[str, str]:
    """Re-evaluate tag heuristics for the supplied models.

    For every model name in `available_models`:
      - If a `model_tags` row exists with `source='override'`, leave it.
      - If a `model_tags` row exists with `source='auto'`, recompute
        the tag and update the row in place if it changed.
      - If no row exists, insert a new `source='auto'` row.

    `yaml_override` is the precedence escape hatch — used by the PUT
    settings handler before persisting so the new YAML drives the
    re-evaluation atomically. If None, reads the persisted setting (or
    the bundled default).

    Returns a `{model: tag}` dict reflecting the post-call state.
    Caller is responsible for committing.
    """
    patterns = _resolve_patterns(session, yaml_override)
    out: dict[str, str] = {}
    for name in available_models:
        existing = session.execute(
            select(ModelTag).where(ModelTag.model == name)
        ).scalar_one_or_none()
        decided = tag_for(name, patterns)
        if existing is None:
            session.add(ModelTag(model=name, tag=decided, source="auto"))
            out[name] = decided
            continue
        if existing.source == "override":
            out[name] = existing.tag
            continue
        # auto row — refresh in place if changed.
        if existing.tag != decided:
            existing.tag = decided
        out[name] = decided
    session.flush()
    return out
