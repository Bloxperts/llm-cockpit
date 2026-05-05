"""Read-only Cortex Conductor data access for blox-cockpit."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class ConductorPaths:
    ssh_host: str
    manifest_path: str
    context_report_path: str
    timeout_seconds: int = 5


class ConductorReadError(RuntimeError):
    """Raised when the Cortex shadow files cannot be read."""


class ConductorSnapshot:
    """Aggregates Conductor shadow manifests without duplicating runtime state."""

    surface_name = "blox-cockpit.conductor"

    def __init__(self, paths: ConductorPaths) -> None:
        self.paths = paths

    def overview(self) -> dict[str, Any]:
        manifests = self._read_manifests()
        return {
            "reachable": True,
            "surface": self.surface_name,
            "source": {
                "ssh_host": self.paths.ssh_host,
                "manifest_path": self.paths.manifest_path,
                "context_report_path": self.paths.context_report_path,
            },
            "updated_at": datetime.now(UTC).isoformat(),
            "manifest_count": len(manifests),
            "latest_manifest": _latest_manifest_summary(manifests),
            "recent_manifests": [_manifest_list_item(item) for item in manifests[-20:]][::-1],
            "overview": _overview_from_manifests(manifests),
        }

    def context_report(self) -> dict[str, Any]:
        raw = self._ssh_cat(self.paths.context_report_path)
        if not raw.strip():
            raise ConductorReadError("context_report_empty")
        report = json.loads(raw)
        return {
            "reachable": True,
            "surface": self.surface_name,
            "source": {
                "ssh_host": self.paths.ssh_host,
                "context_report_path": self.paths.context_report_path,
            },
            "updated_at": datetime.now(UTC).isoformat(),
            "report": report,
        }

    def manifest_detail(self, manifest_id: str) -> dict[str, Any]:
        for item in self._read_manifests():
            if item.get("id") == manifest_id:
                return {
                    "reachable": True,
                    "surface": self.surface_name,
                    "source": {
                        "ssh_host": self.paths.ssh_host,
                        "manifest_path": self.paths.manifest_path,
                    },
                    "updated_at": datetime.now(UTC).isoformat(),
                    "manifest": item,
                }
        raise ConductorReadError(f"manifest_not_found:{manifest_id}")

    def _read_manifests(self) -> tuple[dict[str, Any], ...]:
        raw = self._ssh_cat(self.paths.manifest_path)
        records: list[dict[str, Any]] = []
        for line in raw.splitlines():
            if line.strip():
                records.append(json.loads(line))
        return tuple(records)

    def _ssh_cat(self, path: str) -> str:
        quoted_path = shlex.quote(path)
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={self.paths.timeout_seconds}",
                self.paths.ssh_host,
                f"sudo cat {quoted_path}",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=self.paths.timeout_seconds + 2,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "ssh_read_failed"
            raise ConductorReadError(message)
        return result.stdout


def degraded_response(error: Exception, *, surface: str = ConductorSnapshot.surface_name) -> dict[str, Any]:
    return {
        "reachable": False,
        "surface": surface,
        "updated_at": datetime.now(UTC).isoformat(),
        "error": str(error),
    }


def _overview_from_manifests(manifests: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    call_count = len(manifests)
    cache_hit_count = sum(1 for item in manifests if item.get("extras", {}).get("cache_hit"))
    prompt_budget_errors = sum(
        1 for item in manifests if item.get("outcome", {}).get("error_class") == "prompt_budget_exceeded"
    )
    return {
        "call_count": call_count,
        "failure_count": sum(1 for item in manifests if item.get("outcome", {}).get("status") != "completed"),
        "cache_hit_count": cache_hit_count,
        "cache_hit_rate": round(cache_hit_count / call_count, 6) if call_count else 0.0,
        "fallback_count": sum(1 for item in manifests if item.get("outcome", {}).get("fallback_taken")),
        "prompt_budget_exceeded_rate": round(prompt_budget_errors / call_count, 6) if call_count else 0.0,
        "manifest_coverage_percent": 100.0 if call_count else 0.0,
        "total_cost_usd": round(sum(_realised(item).get("cost_usd", 0.0) for item in manifests), 6),
        "total_tokens_in": sum(_realised(item).get("tokens_in_total", 0) for item in manifests),
        "total_tokens_out": sum(_realised(item).get("tokens_out", 0) for item in manifests),
        "tokens_by_tier": _tokens_by_tier(manifests),
        "spend_by_adapter": _spend_by_adapter(manifests),
        "spend_by_node": _spend_by_node(manifests),
        "fallback_events": _fallback_events(manifests),
        "retrieval_mode_mix": _retrieval_mode_mix(manifests),
    }


def _latest_manifest_summary(manifests: tuple[dict[str, Any], ...]) -> dict[str, Any] | None:
    if not manifests:
        return None
    item = manifests[-1]
    return _manifest_list_item(item)


def _manifest_list_item(item: dict[str, Any]) -> dict[str, Any]:
    realised = _realised(item)
    routing = item.get("extras", {}).get("routing", {})
    return {
        "id": item.get("id"),
        "request_id": item.get("request_id"),
        "session_id": item.get("session_id"),
        "agent": item.get("agent"),
        "at": item.get("at"),
        "adapter": item.get("adapter"),
        "node": routing.get("node_chosen") or item.get("extras", {}).get("node"),
        "capability": item.get("extras", {}).get("capability"),
        "tier": item.get("extras", {}).get("tier"),
        "status": item.get("outcome", {}).get("status"),
        "runtime_mode": item.get("extras", {}).get("runtime_mode") or item.get("extras", {}).get("mode"),
        "tokens_in_total": realised.get("tokens_in_total", 0),
        "tokens_out": realised.get("tokens_out", 0),
        "cost_usd": realised.get("cost_usd", 0.0),
        "retrieval_mode": (realised.get("retrieval") or {}).get("mode"),
        "routing": {
            "context_window_limit": routing.get("context_window_limit"),
            "input_budget_limit": routing.get("input_budget_limit"),
            "routing_reason": routing.get("routing_reason"),
        },
    }


def _tokens_by_tier(manifests: tuple[dict[str, Any], ...]) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = {}
    for item in manifests:
        tier = item.get("extras", {}).get("tier", "unknown")
        bucket = totals.setdefault(tier, {"tokens_in": 0, "tokens_out": 0})
        bucket["tokens_in"] += _realised(item).get("tokens_in_total", 0)
        bucket["tokens_out"] += _realised(item).get("tokens_out", 0)
    return totals


def _spend_by_adapter(manifests: tuple[dict[str, Any], ...]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for item in manifests:
        adapter = item.get("adapter", "unknown")
        totals[adapter] = round(totals.get(adapter, 0.0) + _realised(item).get("cost_usd", 0.0), 6)
    return totals


def _spend_by_node(manifests: tuple[dict[str, Any], ...]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for item in manifests:
        node = item.get("extras", {}).get("routing", {}).get("node_chosen", "unknown")
        totals[node] = round(totals.get(node, 0.0) + _realised(item).get("cost_usd", 0.0), 6)
    return totals


def _fallback_events(manifests: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    events = []
    for item in manifests:
        fallback = item.get("outcome", {}).get("fallback_taken")
        if fallback:
            events.append(
                {
                    "manifest_id": item.get("id"),
                    "capability": item.get("extras", {}).get("capability"),
                    "adapter": item.get("adapter"),
                    "fallback_taken": fallback,
                }
            )
    return events[-20:]


def _retrieval_mode_mix(manifests: tuple[dict[str, Any], ...]) -> dict[str, int]:
    mix: dict[str, int] = {"classic": 0, "agentic": 0, "graph": 0}
    for item in manifests:
        retrieval = _realised(item).get("retrieval") or {}
        mode = retrieval.get("mode") or "classic"
        mix[mode] = mix.get(mode, 0) + 1
    return mix


def _realised(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("realised") or {}
