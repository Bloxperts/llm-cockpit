"""Model-name → tag heuristic per ADR-004 §3.

Public API:
    load_heuristic(path=None) → list[re.Pattern]   # compiled patterns
    tag_for(name, patterns)   → 'chat' | 'code'
    snapshot_tags(session, model_names, patterns)  → dict[str, str]
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from cockpit.models import ModelTag


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
        session.add(ModelTag(model=name, tag=decided, source="heuristic"))
        out[name] = decided
    session.flush()
    return out
