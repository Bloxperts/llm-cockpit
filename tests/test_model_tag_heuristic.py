"""Model-name heuristic unit tests (ADR-004 §3)."""

from __future__ import annotations

import pytest

from cockpit.services.model_tags import load_heuristic, tag_for


@pytest.fixture(scope="module")
def patterns():
    return load_heuristic()


@pytest.mark.parametrize(
    "name,expected",
    [
        ("gemma3:27b", "chat"),
        ("llama3:70b-instruct", "chat"),
        ("mistral:7b", "chat"),
        ("qwen3-coder:30b", "code"),
        ("qwen2.5-coder:7b", "code"),
        ("starcoder:15b", "code"),
        ("codellama:13b", "code"),
        ("deepseek-r1:7b", "code"),
        ("phind-codellama:34b", "code"),
        ("magicoder:6.7b", "code"),
        ("wizardcoder:python", "code"),
    ],
)
def test_tag_for(patterns, name: str, expected: str) -> None:
    assert tag_for(name, patterns) == expected
