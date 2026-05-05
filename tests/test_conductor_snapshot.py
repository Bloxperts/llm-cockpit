"""Conductor dashboard aggregation tests."""

from __future__ import annotations

import subprocess

import pytest

from cockpit.services.conductor import ConductorPaths, ConductorReadError, ConductorSnapshot

MANIFEST_JSONL = """
{"id":"man_1","request_id":"req_1","session_id":"sess_1","agent":"codex","at":"2026-05-05T10:00:00Z","adapter":"shadow-template","realised":{"tokens_in_total":9,"tokens_out":5,"cost_usd":0.0,"retrieval":{"mode":"classic"}},"outcome":{"status":"completed"},"extras":{"tier":"standard","routing":{"node_chosen":"cortex"},"capability":"general_chat","runtime_mode":"shadow"}}
{"id":"man_2","request_id":"req_2","session_id":"sess_1","agent":"codex","at":"2026-05-05T10:01:00Z","adapter":"shadow-template","realised":{"tokens_in_total":12,"tokens_out":7,"cost_usd":0.0,"retrieval":{"mode":"agentic"}},"outcome":{"status":"failed","fallback_taken":"upgrade"},"extras":{"tier":"reasoning","routing":{"node_chosen":"cortex"},"capability":"deep_reasoning","runtime_mode":"shadow"}}
""".strip()


def test_overview_aggregates_remote_manifest(monkeypatch: pytest.MonkeyPatch):
    def fake_run(*args, **kwargs):  # noqa: ANN002, ANN003
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=MANIFEST_JSONL, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    snapshot = ConductorSnapshot(
        ConductorPaths(
            ssh_host="bloxperts@cortex",
            manifest_path="/var/lib/agentic-blox/conductor/manifests.jsonl",
            context_report_path="/var/lib/agentic-blox/conductor/report.json",
        )
    )
    data = snapshot.overview()

    assert data["reachable"] is True
    assert data["manifest_count"] == 2
    assert data["overview"]["call_count"] == 2
    assert data["overview"]["failure_count"] == 1
    assert data["overview"]["fallback_count"] == 1
    assert data["overview"]["total_tokens_in"] == 21
    assert data["overview"]["retrieval_mode_mix"]["classic"] == 1
    assert data["overview"]["retrieval_mode_mix"]["agentic"] == 1
    assert data["latest_manifest"]["id"] == "man_2"
    assert data["latest_manifest"]["node"] == "cortex"


def test_ssh_failure_is_explicit(monkeypatch: pytest.MonkeyPatch):
    def fake_run(*args, **kwargs):  # noqa: ANN002, ANN003
        return subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="permission denied")

    monkeypatch.setattr(subprocess, "run", fake_run)

    snapshot = ConductorSnapshot(
        ConductorPaths(
            ssh_host="bloxperts@cortex",
            manifest_path="/var/lib/agentic-blox/conductor/manifests.jsonl",
            context_report_path="/var/lib/agentic-blox/conductor/report.json",
        )
    )

    with pytest.raises(ConductorReadError, match="permission denied"):
        snapshot.overview()
