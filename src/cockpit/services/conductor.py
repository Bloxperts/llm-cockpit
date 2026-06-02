"""Read-only Conductor data access for blox-cockpit."""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cockpit.config import DEFAULT_OLLAMA_URL


@dataclass(frozen=True)
class ConductorPaths:
    ssh_host: str
    manifest_path: str
    context_report_path: str
    bloxguard_config_path: str = "/var/lib/agentic-blox/conductor/bloxguard-config.json"
    bloxguard_secrets_path: str = "/etc/agentic-blox/secrets.env"
    timeout_seconds: int = 5


class ConductorReadError(RuntimeError):
    """Raised when Conductor shadow files cannot be read."""


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

    def bloxguard_summary(self) -> dict[str, Any]:
        manifests = self._read_manifests()
        return {
            "reachable": True,
            "surface": "blox-cockpit.bloxguard",
            "source": {
                "ssh_host": self.paths.ssh_host,
                "manifest_path": self.paths.manifest_path,
            },
            "updated_at": datetime.now(UTC).isoformat(),
            **_bloxguard_summary_from_manifests(manifests),
        }

    def bloxguard_config(self) -> dict[str, Any]:
        raw = self._read_text(self.paths.bloxguard_config_path)
        if not raw.strip():
            raise ConductorReadError("bloxguard_config_empty")
        config = json.loads(raw)
        health = _bloxguard_config_health(config)
        validation = _validate_bloxguard_runtime_config(config)
        return {
            "reachable": True,
            "surface": "blox-cockpit.bloxguard.config",
            "source": {
                "ssh_host": self.paths.ssh_host,
                "config_path": self.paths.bloxguard_config_path,
            },
            "updated_at": datetime.now(UTC).isoformat(),
            "config": config,
            "node_health": health["node_health"],
            "candidate_health": health["candidate_health"],
            "config_contract": validation,
        }

    def bloxguard_module_contract(self) -> dict[str, Any]:
        return _bloxguard_module_page_contract()

    def bloxguard_config_dry_run(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        candidate = self._bloxguard_config_candidate(payload or {})
        validation = _validate_bloxguard_runtime_config(candidate)
        return {
            "reachable": True,
            "surface": "blox-cockpit.bloxguard.config.dry_run",
            "source": {
                "ssh_host": self.paths.ssh_host,
                "config_path": self.paths.bloxguard_config_path,
            },
            "updated_at": datetime.now(UTC).isoformat(),
            "dry_run": True,
            "ok": bool(validation.get("ok")),
            "config_contract": validation,
            "changed_fields": _changed_top_level_fields(self._read_bloxguard_config(), candidate),
        }

    def bloxguard_manifest_integrity(self, jsonl_path: str | None = None) -> dict[str, Any]:
        path = jsonl_path or self.paths.manifest_path
        integrity_check_jsonl = _load_bloxguard_symbol("blox_guard.manifest", "integrity_check_jsonl")
        result = integrity_check_jsonl(path)
        return {
            "reachable": True,
            "surface": "blox-cockpit.bloxguard.operations.manifest_integrity",
            "source": {"ssh_host": self.paths.ssh_host, "manifest_path": path},
            "updated_at": datetime.now(UTC).isoformat(),
            "result": result,
        }

    def bloxguard_manifest_backfill(
        self,
        *,
        jsonl_path: str | None = None,
        sqlite_path: str | None = None,
    ) -> dict[str, Any]:
        source_path = jsonl_path or self.paths.manifest_path
        target_path = sqlite_path or self._default_manifest_sqlite_path()
        backfill_jsonl_to_sqlite = _load_bloxguard_symbol("blox_guard.manifest", "backfill_jsonl_to_sqlite")
        result = backfill_jsonl_to_sqlite(source_path, target_path)
        return {
            "reachable": True,
            "surface": "blox-cockpit.bloxguard.operations.manifest_backfill",
            "source": {
                "ssh_host": self.paths.ssh_host,
                "manifest_path": source_path,
                "sqlite_path": target_path,
            },
            "updated_at": datetime.now(UTC).isoformat(),
            "result": result,
        }

    def migrate_bloxguard_manifest_config(
        self,
        *,
        sqlite_path: str | None = None,
        jsonl_replay_enabled: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        config_path = self.paths.bloxguard_config_path
        if not (_is_local_ssh_host(self.paths.ssh_host) or Path(config_path).exists()):
            raise ConductorReadError("bloxguard_config_migration_requires_local_host")
        target_path = sqlite_path or self._default_manifest_sqlite_path()
        migrate_manifest_config = _load_bloxguard_symbol("blox_guard.admin", "migrate_manifest_config")
        result = migrate_manifest_config(
            config_path,
            target_path,
            jsonl_replay_enabled=jsonl_replay_enabled,
            dry_run=dry_run,
        )
        if not dry_run:
            self._append_bloxguard_config_audit(
                action="bloxguard.manifest.config.migrate",
                resource="manifest",
                patch={
                    "sqlite_path": target_path,
                    "jsonl_replay_enabled": jsonl_replay_enabled,
                    "dry_run": dry_run,
                },
                validation={"ok": True, "source": "blox_guard.admin.migrate_manifest_config"},
                backup_path=result.get("backup_path"),
            )
        return {
            "reachable": True,
            "surface": "blox-cockpit.bloxguard.operations.migrate_manifest_config",
            "source": {
                "ssh_host": self.paths.ssh_host,
                "config_path": config_path,
                "sqlite_path": target_path,
            },
            "updated_at": datetime.now(UTC).isoformat(),
            "result": result,
        }

    def bloxguard_call_logs(self, limit: int = 50) -> dict[str, Any]:
        manifests = self._read_manifests()
        bounded_limit = min(max(limit, 1), 200)
        recent = sorted(manifests, key=lambda item: _manifest_timestamp(item) or datetime.min.replace(tzinfo=UTC))
        return {
            "reachable": True,
            "surface": "blox-cockpit.bloxguard.logs",
            "source": {
                "ssh_host": self.paths.ssh_host,
                "manifest_path": self.paths.manifest_path,
            },
            "updated_at": datetime.now(UTC).isoformat(),
            "limit": bounded_limit,
            "total": len(manifests),
            "debug_capture": self._debug_capture_state(),
            "calls": [_bloxguard_call_log_item(item) for item in recent[-bounded_limit:]][::-1],
        }

    def bloxguard_call_detail(self, manifest_id: str) -> dict[str, Any]:
        for item in self._read_manifests():
            if _manifest_identity(item) == manifest_id:
                return {
                    "reachable": True,
                    "surface": "blox-cockpit.bloxguard.logs.detail",
                    "source": {
                        "ssh_host": self.paths.ssh_host,
                        "manifest_path": self.paths.manifest_path,
                    },
                    "updated_at": datetime.now(UTC).isoformat(),
                    "call": _bloxguard_call_log_item(item),
                    "context_debug": _context_debug_payload(item),
                    "manifest": item,
                }
        raise ConductorReadError(f"bloxguard_call_not_found:{manifest_id}")

    def update_bloxguard_lane(self, lane_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        raw = self._read_text(self.paths.bloxguard_config_path)
        if not raw.strip():
            raise ConductorReadError("bloxguard_config_empty")
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise ConductorReadError("bloxguard_config_invalid")
        lanes = config.get("lanes")
        if not isinstance(lanes, list):
            raise ConductorReadError("bloxguard_config_has_no_lanes")

        for lane in lanes:
            if not isinstance(lane, dict) or lane.get("id") != lane_id:
                continue
            for field, value in patch.items():
                if field not in _EDITABLE_LANE_FIELDS:
                    raise ConductorReadError(f"bloxguard_lane_field_not_editable:{field}")
                lane[field] = value
            self._write_bloxguard_config(
                config,
                audit_action="bloxguard.config.lane.update",
                audit_resource=lane_id,
                audit_patch=patch,
            )
            return self.bloxguard_config()

        raise ConductorReadError(f"bloxguard_lane_not_found:{lane_id}")

    def add_bloxguard_lane(self, lane: dict[str, Any]) -> dict[str, Any]:
        raw = self._read_text(self.paths.bloxguard_config_path)
        if not raw.strip():
            raise ConductorReadError("bloxguard_config_empty")
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise ConductorReadError("bloxguard_config_invalid")
        lanes = config.get("lanes")
        if not isinstance(lanes, list):
            lanes = []
            config["lanes"] = lanes

        lane_id = str(lane.get("id") or "").strip()
        if not lane_id:
            raise ConductorReadError("bloxguard_lane_id_required")
        if any(isinstance(item, dict) and item.get("id") == lane_id for item in lanes):
            raise ConductorReadError(f"bloxguard_lane_already_exists:{lane_id}")

        new_lane: dict[str, Any] = {"id": lane_id}
        for field, value in lane.items():
            if field == "id":
                continue
            if field not in _EDITABLE_LANE_FIELDS:
                raise ConductorReadError(f"bloxguard_lane_field_not_editable:{field}")
            new_lane[field] = value
        new_lane.setdefault("enabled", True)
        new_lane.setdefault("candidates", [])
        lanes.append(new_lane)
        self._write_bloxguard_config(
            config,
            audit_action="bloxguard.config.lane.add",
            audit_resource=lane_id,
            audit_patch=lane,
        )
        return self.bloxguard_config()

    def delete_bloxguard_lane(self, lane_id: str) -> dict[str, Any]:
        raw = self._read_text(self.paths.bloxguard_config_path)
        if not raw.strip():
            raise ConductorReadError("bloxguard_config_empty")
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise ConductorReadError("bloxguard_config_invalid")
        lanes = config.get("lanes")
        if not isinstance(lanes, list):
            raise ConductorReadError("bloxguard_config_has_no_lanes")

        for index, lane in enumerate(lanes):
            if isinstance(lane, dict) and lane.get("id") == lane_id:
                del lanes[index]
                self._write_bloxguard_config(
                    config,
                    audit_action="bloxguard.config.lane.delete",
                    audit_resource=lane_id,
                )
                return self.bloxguard_config()

        raise ConductorReadError(f"bloxguard_lane_not_found:{lane_id}")

    def update_bloxguard_candidate(
        self,
        candidate_key: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        raw = self._read_text(self.paths.bloxguard_config_path)
        if not raw.strip():
            raise ConductorReadError("bloxguard_config_empty")
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise ConductorReadError("bloxguard_config_invalid")
        lanes = config.get("lanes")
        if not isinstance(lanes, list):
            raise ConductorReadError("bloxguard_config_has_no_lanes")

        for lane in lanes:
            if not isinstance(lane, dict):
                continue
            candidates = lane.get("candidates")
            if not isinstance(candidates, list):
                continue
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                if _candidate_config_matches(lane, candidate, candidate_key):
                    for field, value in patch.items():
                        if field not in _EDITABLE_CANDIDATE_FIELDS:
                            raise ConductorReadError(f"bloxguard_candidate_field_not_editable:{field}")
                        candidate[field] = value
                    self._write_bloxguard_config(
                        config,
                        audit_action="bloxguard.config.candidate.update",
                        audit_resource=candidate_key,
                        audit_patch=patch,
                    )
                    return self.bloxguard_config()

        raise ConductorReadError(f"bloxguard_candidate_not_found:{candidate_key}")

    def add_bloxguard_candidate(
        self,
        lane_id: str,
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        raw = self._read_text(self.paths.bloxguard_config_path)
        if not raw.strip():
            raise ConductorReadError("bloxguard_config_empty")
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise ConductorReadError("bloxguard_config_invalid")
        lanes = config.get("lanes")
        if not isinstance(lanes, list):
            raise ConductorReadError("bloxguard_config_has_no_lanes")

        lane_id = lane_id.strip()
        if not lane_id:
            raise ConductorReadError("bloxguard_candidate_lane_required")
        candidate_id = str(candidate.get("id") or "").strip()
        if candidate_id.startswith(f"{lane_id}."):
            candidate_id = candidate_id[len(lane_id) + 1 :]
        if not candidate_id:
            raise ConductorReadError("bloxguard_candidate_id_required")

        for lane in lanes:
            if not isinstance(lane, dict) or lane.get("id") != lane_id:
                continue
            candidates = lane.get("candidates")
            if not isinstance(candidates, list):
                candidates = []
                lane["candidates"] = candidates
            if any(
                isinstance(item, dict)
                and _candidate_config_matches(lane, item, f"{lane_id}.{candidate_id}")
                for item in candidates
            ):
                raise ConductorReadError(f"bloxguard_candidate_already_exists:{lane_id}.{candidate_id}")
            new_candidate: dict[str, Any] = {"id": candidate_id}
            for field, value in candidate.items():
                if field == "id":
                    continue
                if field not in _EDITABLE_CANDIDATE_FIELDS:
                    raise ConductorReadError(f"bloxguard_candidate_field_not_editable:{field}")
                new_candidate[field] = value
            new_candidate.setdefault("enabled", True)
            candidates.append(new_candidate)
            self._write_bloxguard_config(
                config,
                audit_action="bloxguard.config.candidate.add",
                audit_resource=f"{lane_id}.{candidate_id}",
                audit_patch={**candidate, "lane_id": lane_id},
            )
            return self.bloxguard_config()

        raise ConductorReadError(f"bloxguard_lane_not_found:{lane_id}")

    def delete_bloxguard_candidate(self, candidate_key: str) -> dict[str, Any]:
        raw = self._read_text(self.paths.bloxguard_config_path)
        if not raw.strip():
            raise ConductorReadError("bloxguard_config_empty")
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise ConductorReadError("bloxguard_config_invalid")
        lanes = config.get("lanes")
        if not isinstance(lanes, list):
            raise ConductorReadError("bloxguard_config_has_no_lanes")

        for lane in lanes:
            if not isinstance(lane, dict):
                continue
            candidates = lane.get("candidates")
            if not isinstance(candidates, list):
                continue
            for index, candidate in enumerate(candidates):
                if isinstance(candidate, dict) and _candidate_config_matches(lane, candidate, candidate_key):
                    del candidates[index]
                    self._write_bloxguard_config(
                        config,
                        audit_action="bloxguard.config.candidate.delete",
                        audit_resource=candidate_key,
                    )
                    return self.bloxguard_config()

        raise ConductorReadError(f"bloxguard_candidate_not_found:{candidate_key}")

    def update_bloxguard_modes(self, patch: dict[str, Any]) -> dict[str, Any]:
        raw = self._read_text(self.paths.bloxguard_config_path)
        if not raw.strip():
            raise ConductorReadError("bloxguard_config_empty")
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise ConductorReadError("bloxguard_config_invalid")

        if "enabled" in patch:
            config["enabled"] = bool(patch["enabled"])
        if "mode" in patch:
            mode = _validate_choice("mode", patch["mode"], _VALID_BLOXGUARD_MODES)
            config["mode"] = mode
        if "bypass_mode" in patch:
            bypass_mode = _validate_choice("bypass_mode", patch["bypass_mode"], _VALID_BLOXGUARD_BYPASS_MODES)
            config["bypass_mode"] = bypass_mode
        if "compression_mode" in patch:
            compression_mode = _validate_choice(
                "compression_mode",
                patch["compression_mode"],
                _VALID_BLOXGUARD_COMPRESSION_MODES,
            )
            config["compression_mode"] = compression_mode
        if "cloud_enabled" in patch:
            route_policy = _ensure_dict(config, "route_policy")
            route_policy["cloud_enabled"] = bool(patch["cloud_enabled"])
        if "cloud_monthly_budget_usd" in patch:
            budget = patch["cloud_monthly_budget_usd"]
            if budget is not None:
                budget = float(budget)
                if budget < 0:
                    raise ConductorReadError("invalid_cloud_monthly_budget_usd")
            config["monthly_budget_usd"] = budget
            route_policy = _ensure_dict(config, "route_policy")
            route_policy["cloud_monthly_budget_usd"] = budget
            route_policy["cloud_cost_currency"] = "USD"
            config.pop("monthly_budget_chf", None)
            route_policy.pop("cloud_monthly_budget_chf", None)
            route_policy.pop("monthly_budget_chf", None)
        if "debug_capture_enabled" in patch:
            debug_capture = _ensure_dict(config, "debug_capture")
            debug_capture["enabled"] = bool(patch["debug_capture_enabled"])
            debug_capture.setdefault("capture_points", ["input", "compressed", "injected"])
        if "debug_store_raw_context" in patch:
            debug_capture = _ensure_dict(config, "debug_capture")
            debug_capture["store_raw_context"] = bool(patch["debug_store_raw_context"])
        if "debug_retention_days" in patch:
            retention = patch["debug_retention_days"]
            if retention is not None:
                retention = int(retention)
                if retention < 1:
                    raise ConductorReadError("invalid_debug_retention_days")
            debug_capture = _ensure_dict(config, "debug_capture")
            debug_capture["retention_days"] = retention

        self._write_bloxguard_config(
            config,
            audit_action="bloxguard.config.modes.update",
            audit_patch=patch,
        )
        return self.bloxguard_config()

    def update_bloxguard_cost_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        raw = self._read_text(self.paths.bloxguard_config_path)
        if not raw.strip():
            raise ConductorReadError("bloxguard_config_empty")
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise ConductorReadError("bloxguard_config_invalid")

        if "cloud_monthly_budget_usd" in patch:
            budget = patch["cloud_monthly_budget_usd"]
            if budget is not None:
                budget = float(budget)
                if budget < 0:
                    raise ConductorReadError("invalid_cloud_monthly_budget_usd")
            config["monthly_budget_usd"] = budget
            route_policy = _ensure_dict(config, "route_policy")
            route_policy["cloud_monthly_budget_usd"] = budget
            route_policy["cloud_cost_currency"] = "USD"
            config.pop("monthly_budget_chf", None)
            route_policy.pop("cloud_monthly_budget_chf", None)
            route_policy.pop("monthly_budget_chf", None)

        openai_api = patch.get("openai_api")
        if isinstance(openai_api, dict) and "models" in openai_api:
            models = openai_api.get("models")
            if not isinstance(models, dict):
                raise ConductorReadError("bloxguard_pricing_models_invalid")
            pricing = _ensure_dict(config, "pricing")
            pricing_openai = _ensure_dict(pricing, "openai_api")
            pricing_openai["currency"] = "USD"
            pricing_openai["updated_at"] = datetime.now(UTC).isoformat()
            current_models = pricing_openai.get("models")
            if not isinstance(current_models, dict):
                current_models = {}
                pricing_openai["models"] = current_models
            for model, values in models.items():
                model_name = str(model).strip()
                if not model_name:
                    raise ConductorReadError("bloxguard_pricing_model_required")
                if not isinstance(values, dict):
                    raise ConductorReadError(f"bloxguard_pricing_model_invalid:{model_name}")
                current_models[model_name] = _validated_pricing_model(model_name, values)

        self._write_bloxguard_config(
            config,
            audit_action="bloxguard.config.cost.update",
            audit_patch=patch,
        )
        return self.bloxguard_config()

    def update_bloxguard_policy_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        raw = self._read_text(self.paths.bloxguard_config_path)
        if not raw.strip():
            raise ConductorReadError("bloxguard_config_empty")
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise ConductorReadError("bloxguard_config_invalid")
        policy = _ensure_dict(config, "policy")
        for field, value in patch.items():
            if field != "tool_permissions":
                raise ConductorReadError(f"bloxguard_policy_field_not_editable:{field}")
            if value is None:
                policy["tool_permissions"] = []
            elif isinstance(value, list):
                policy["tool_permissions"] = [str(item).strip() for item in value if str(item).strip()]
            else:
                raise ConductorReadError("bloxguard_policy_tool_permissions_invalid")
        self._write_bloxguard_config(
            config,
            audit_action="bloxguard.config.policy.update",
            audit_patch=patch,
        )
        return self.bloxguard_config()

    def update_bloxguard_redaction_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        raw = self._read_text(self.paths.bloxguard_config_path)
        if not raw.strip():
            raise ConductorReadError("bloxguard_config_empty")
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise ConductorReadError("bloxguard_config_invalid")
        redaction = _ensure_dict(config, "redaction")
        for field, value in patch.items():
            if field not in _EDITABLE_REDACTION_FIELDS:
                raise ConductorReadError(f"bloxguard_redaction_field_not_editable:{field}")
            if field in {"enabled", "fail_closed", "store_maps"}:
                redaction[field] = bool(value)
            elif field in {"strategy", "map_path"}:
                redaction[field] = str(value).strip() if value not in (None, "") else None
        self._write_bloxguard_config(
            config,
            audit_action="bloxguard.config.redaction.update",
            audit_patch=patch,
        )
        return self.bloxguard_config()

    def bloxguard_provider_secrets(self) -> dict[str, Any]:
        raw = self._read_optional_text(self.paths.bloxguard_secrets_path)
        values = _parse_env_file(raw)
        return {
            "reachable": True,
            "surface": "blox-cockpit.bloxguard.secrets",
            "source": {
                "ssh_host": self.paths.ssh_host,
                "secrets_path": self.paths.bloxguard_secrets_path,
            },
            "updated_at": datetime.now(UTC).isoformat(),
            "providers": [_provider_secret_row(provider, values) for provider in _BLOXGUARD_PROVIDER_SECRETS],
        }

    def update_bloxguard_provider_secret(
        self,
        provider_id: str,
        *,
        value: str | None,
        clear: bool = False,
    ) -> dict[str, Any]:
        provider = _provider_secret_definition(provider_id)
        raw = self._read_optional_text(self.paths.bloxguard_secrets_path)
        updated = _update_env_secret(
            raw,
            env_var=provider["env_var"],
            aliases=provider["aliases"],
            value=value,
            clear=clear,
        )
        self._write_secret_text(self.paths.bloxguard_secrets_path, updated)
        return self.bloxguard_provider_secrets()

    def context_report(self) -> dict[str, Any]:
        raw = self._read_text(self.paths.context_report_path)
        if not raw.strip():
            raise ConductorReadError("context_report_empty")
        report = json.loads(raw)
        quality = _context_quality(report)
        return {
            "reachable": True,
            "surface": self.surface_name,
            "source": {
                "ssh_host": self.paths.ssh_host,
                "context_report_path": self.paths.context_report_path,
            },
            "updated_at": datetime.now(UTC).isoformat(),
            "report": report,
            "quality": quality,
        }

    def manifest_detail(self, manifest_id: str) -> dict[str, Any]:
        for item in self._read_manifests():
            if _manifest_identity(item) == manifest_id:
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
        raw = self._read_text(self.paths.manifest_path)
        records: list[dict[str, Any]] = []
        for line in raw.splitlines():
            if line.strip():
                records.append(json.loads(line))
        return tuple(records)

    def _debug_capture_state(self) -> dict[str, Any]:
        try:
            raw = self._read_optional_text(self.paths.bloxguard_config_path)
            config = json.loads(raw) if raw.strip() else {}
        except Exception:
            return {"enabled": False, "store_raw_context": False, "retention_days": None, "status": "unknown"}
        debug_capture = config.get("debug_capture") if isinstance(config, dict) else None
        if not isinstance(debug_capture, dict):
            return {"enabled": False, "store_raw_context": False, "retention_days": None, "status": "not_configured"}
        return {
            "enabled": bool(debug_capture.get("enabled")),
            "store_raw_context": bool(debug_capture.get("store_raw_context")),
            "retention_days": debug_capture.get("retention_days"),
            "capture_points": debug_capture.get("capture_points") or ["input", "compressed", "injected"],
            "status": "configured",
        }

    def _read_text(self, path: str) -> str:
        if _is_local_ssh_host(self.paths.ssh_host):
            return _local_cat(path, self.paths.timeout_seconds)
        try:
            return self._ssh_cat(path)
        except ConductorReadError as exc:
            if "Host key verification failed" in str(exc) and Path(path).exists():
                return _local_cat(path, self.paths.timeout_seconds)
            raise

    def _read_optional_text(self, path: str) -> str:
        try:
            return self._read_text(path)
        except ConductorReadError as exc:
            if "No such file" in str(exc) or "not found" in str(exc):
                return ""
            raise

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

    def _read_bloxguard_config(self) -> dict[str, Any]:
        raw = self._read_text(self.paths.bloxguard_config_path)
        if not raw.strip():
            raise ConductorReadError("bloxguard_config_empty")
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise ConductorReadError("bloxguard_config_invalid")
        return config

    def _bloxguard_config_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "config" in payload:
            config = payload["config"]
            if not isinstance(config, dict):
                raise ConductorReadError("bloxguard_dry_run_config_invalid")
            return config
        config = self._read_bloxguard_config()
        patch = payload.get("patch")
        if patch is None:
            return config
        if not isinstance(patch, dict):
            raise ConductorReadError("bloxguard_dry_run_patch_invalid")
        candidate = json.loads(json.dumps(config))
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(candidate.get(key), dict):
                candidate[key] = {**candidate[key], **value}
            else:
                candidate[key] = value
        return candidate

    def _default_manifest_sqlite_path(self) -> str:
        config = self._read_bloxguard_config()
        manifest = config.get("manifest")
        if isinstance(manifest, dict) and manifest.get("sqlite_path"):
            return str(manifest["sqlite_path"])
        return str(Path(self.paths.manifest_path).with_suffix(".sqlite"))

    def _write_bloxguard_config(
        self,
        config: dict[str, Any],
        *,
        audit_action: str | None = None,
        audit_resource: str | None = None,
        audit_patch: dict[str, Any] | None = None,
    ) -> None:
        validation = _validate_bloxguard_runtime_config(config)
        if not validation.get("ok"):
            raise ConductorReadError(f"bloxguard_config_validation_failed:{validation.get('error')}")
        path = self.paths.bloxguard_config_path
        if _is_local_ssh_host(self.paths.ssh_host) or Path(path).exists():
            backup_path = _local_write_json(path, config)
            if audit_action:
                self._append_bloxguard_config_audit(
                    action=audit_action,
                    resource=audit_resource,
                    patch=audit_patch or {},
                    validation=validation,
                    backup_path=backup_path,
                )
            return
        raise ConductorReadError("bloxguard_config_write_requires_local_host")

    def _append_bloxguard_config_audit(
        self,
        *,
        action: str,
        resource: str | None = None,
        patch: dict[str, Any] | None = None,
        validation: dict[str, Any] | None = None,
        backup_path: str | None = None,
    ) -> None:
        config_path = Path(self.paths.bloxguard_config_path)
        audit_path = config_path.with_name("bloxguard-config-audit.jsonl")
        event = {
            "schema_version": "bloxguard.config_audit.v1",
            "ts": datetime.now(UTC).isoformat(),
            "action": action,
            "resource": resource,
            "config_path": str(config_path),
            "backup_path": backup_path,
            "validation": validation or {},
            "patch": _redact_config_audit_payload(patch or {}),
        }
        _append_local_jsonl(str(audit_path), event)

    def _write_secret_text(self, path: str, content: str) -> None:
        if _is_local_ssh_host(self.paths.ssh_host) or Path(path).exists():
            _local_write_text(path, content, mode=0o600)
            return
        raise ConductorReadError("bloxguard_secret_write_requires_local_host")


def _local_cat(path: str, timeout_seconds: int) -> str:
    file_path = Path(path)
    try:
        return file_path.read_text(encoding="utf-8")
    except OSError as direct_exc:
        result = subprocess.run(
            ["sudo", "-n", "cat", str(file_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 2,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or str(direct_exc)
            raise ConductorReadError(message) from direct_exc
        return result.stdout


def _local_write_json(path: str, payload: dict[str, Any]) -> str | None:
    file_path = Path(path)
    formatted = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    backup_path = file_path.with_name(f"{file_path.name}.bak-{stamp}")
    temp_path = file_path.with_name(f".{file_path.name}.tmp-{stamp}")
    created_backup: str | None = None
    try:
        previous = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
        try:
            if previous:
                backup_path.write_text(previous, encoding="utf-8")
                created_backup = str(backup_path)
            temp_path.write_text(formatted, encoding="utf-8")
            temp_path.replace(file_path)
        except OSError:
            file_path.write_text(formatted, encoding="utf-8")
            temp_path.unlink(missing_ok=True)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise ConductorReadError(str(exc)) from exc
    return created_backup


def _append_local_jsonl(path: str, payload: dict[str, Any]) -> None:
    file_path = Path(path)
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError as exc:
        raise ConductorReadError(str(exc)) from exc


def _local_write_text(path: str, content: str, *, mode: int) -> None:
    file_path = Path(path)
    parent = file_path.parent
    if (file_path.exists() and os.access(file_path, os.W_OK)) or (
        not file_path.exists() and os.access(parent, os.W_OK)
    ):
        _direct_write_text(file_path, content, mode=mode)
        return
    _sudo_write_text(file_path, content, mode=mode)


def _direct_write_text(path: Path, content: str, *, mode: int) -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    backup_path = path.with_name(f"{path.name}.bak-{stamp}")
    temp_path = path.with_name(f".{path.name}.tmp-{stamp}")
    try:
        previous = path.read_text(encoding="utf-8") if path.exists() else ""
        if previous:
            backup_path.write_text(previous, encoding="utf-8")
        temp_path.write_text(content, encoding="utf-8")
        temp_path.chmod(mode)
        temp_path.replace(path)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise ConductorReadError(str(exc)) from exc


def _sudo_write_text(path: Path, content: str, *, mode: int) -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    backup_path = path.with_name(f"{path.name}.bak-{stamp}")
    temp_path = path.with_name(f".{path.name}.tmp-{stamp}")
    if path.exists():
        _run_sudo(["cp", str(path), str(backup_path)])
    _run_sudo_input(["tee", str(temp_path)], content)
    _run_sudo(["chmod", f"{mode:o}", str(temp_path)])
    _run_sudo(["chown", "root:root", str(temp_path)])
    _run_sudo(["mv", str(temp_path), str(path)])


def _run_sudo(args: list[str]) -> None:
    result = subprocess.run(
        ["sudo", "-n", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=7,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "sudo_write_failed"
        raise ConductorReadError(message)


def _run_sudo_input(args: list[str], content: str) -> None:
    result = subprocess.run(
        ["sudo", "-n", *args],
        input=content,
        check=False,
        capture_output=True,
        text=True,
        timeout=7,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "sudo_write_failed"
        raise ConductorReadError(message)


def _is_local_ssh_host(ssh_host: str) -> bool:
    host = ssh_host.rsplit("@", 1)[-1].strip().lower()
    if host in {"", "local", "localhost", "127.0.0.1", "::1"}:
        return True
    local_names = {
        socket.gethostname().lower(),
        socket.getfqdn().lower(),
        socket.gethostname().split(".", 1)[0].lower(),
        socket.getfqdn().split(".", 1)[0].lower(),
    }
    return host in local_names


def degraded_response(error: Exception, *, surface: str = ConductorSnapshot.surface_name) -> dict[str, Any]:
    return {
        "reachable": False,
        "surface": surface,
        "updated_at": datetime.now(UTC).isoformat(),
        "error": str(error),
    }


def _bloxguard_summary_from_manifests(manifests: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    fallback_count = sum(1 for item in manifests if _fallback_used(item))
    budget_errors = sum(1 for item in manifests if _classified_error(item) == "prompt_budget_exceeded")
    privacy_denials = sum(1 for item in manifests if _classified_error(item) in {"privacy_capacity_blocked", "policy_denied"})
    anchor = _cost_anchor(manifests)
    context_manifests = _manifests_in_window(manifests, anchor - timedelta(days=7), anchor + timedelta(seconds=1))
    context_health = _context_health(context_manifests)
    today_start = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    month_start = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today_manifests = _manifests_in_window(manifests, today_start, tomorrow_start)
    month_manifests = _manifests_in_window(manifests, month_start, tomorrow_start)
    estimated_cost = round(sum(_cost_estimate(item) for item in today_manifests), 6)
    month_cost = round(sum(_cost_estimate(item) for item in month_manifests), 6)
    oauth_units = sum(_ledger(item).get("oauth_units_est", 0) or 0 for item in today_manifests)
    image_units = sum(_ledger(item).get("image_units_est", 0) or 0 for item in today_manifests)
    local_calls = sum(1 for item in today_manifests if _provider_class(item) in {"local", "local_ollama"} or item.get("adapter") in {"shadow-template", "template"})
    manifest_state = "healthy" if manifests else "unknown"
    status = "healthy"
    if privacy_denials or budget_errors:
        status = "degraded"
    if manifest_state == "unknown":
        status = "unknown"
    return {
        "status": status,
        "today_cost": {
            "estimated_cost_usd": estimated_cost,
            "oauth_units": oauth_units,
            "image_units": image_units,
            "local_calls": local_calls,
        },
        "month_cost": {
            "estimated_cost_usd": month_cost,
            "budget_usd": None,
        },
        "cost_trend": _cost_trend(manifests, anchor=anchor, days=7),
        "cost_history": _cost_history(manifests, anchor=anchor, days=7),
        "cost_drivers": _cost_drivers(manifests, anchor=anchor, days=7),
        "active_jobs": {},
        "queue_depth": {},
        "lane_usage_7d": _counts(_lane_id(item) for item in context_manifests),
        "candidate_usage_7d": _counts(_candidate_id(item) for item in context_manifests),
        "fallbacks_24h": fallback_count,
        "context_pressure": {
            "prompt_budget_exceeded": budget_errors,
            "compression_used": context_health["compressed_calls"],
            "heavy_slot_requests": 0,
            "context_pressure_calls": context_health["context_pressure_calls"],
            "hard_reduction_calls": context_health["hard_reduction_calls"],
            "quality_issue_calls": context_health["quality_issue_calls"],
        },
        "context_health": context_health,
        "privacy_denials": privacy_denials,
        "manifest_state": {
            "status": manifest_state,
            "last_successful_write": (
                manifests[-1].get("completed_at") or manifests[-1].get("created_at") or manifests[-1].get("at")
                if manifests
                else None
            ),
        },
    }


def _cost_anchor(manifests: tuple[dict[str, Any], ...]) -> datetime:
    timestamps = [ts for item in manifests if (ts := _manifest_timestamp(item)) is not None]
    return max(timestamps) if timestamps else datetime.now(UTC)


def _manifest_timestamp(item: dict[str, Any]) -> datetime | None:
    raw = item.get("completed_at") or item.get("created_at") or item.get("at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _manifests_in_window(
    manifests: tuple[dict[str, Any], ...],
    start: datetime,
    end: datetime,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        item
        for item in manifests
        if (timestamp := _manifest_timestamp(item)) is not None and start <= timestamp < end
    )


def _period_cost(manifests: tuple[dict[str, Any], ...], start: datetime, end: datetime) -> float:
    return round(sum(_cost_estimate(item) for item in _manifests_in_window(manifests, start, end)), 6)


def _cost_trend(manifests: tuple[dict[str, Any], ...], *, anchor: datetime, days: int) -> dict[str, Any]:
    current_end = anchor + timedelta(seconds=1)
    current_start = current_end - timedelta(days=days)
    previous_start = current_start - timedelta(days=days)
    current = _period_cost(manifests, current_start, current_end)
    previous = _period_cost(manifests, previous_start, current_start)
    return {
        "period_days": days,
        "current_estimated_cost_usd": current,
        "previous_estimated_cost_usd": previous,
        **_trend_fields(current, previous),
    }


def _trend_fields(current: float, previous: float) -> dict[str, Any]:
    diff = round(current - previous, 6)
    if previous == 0:
        pct_change = None
        direction = "up" if current > 0 else "flat"
    else:
        pct_change = round((diff / previous) * 100, 2)
        if abs(diff) < 0.01 or abs(pct_change) < 10:
            direction = "flat"
        elif diff > 0:
            direction = "up"
        else:
            direction = "down"
    return {
        "direction": direction,
        "absolute_change_usd": diff,
        "pct_change": pct_change,
    }


def _cost_history(manifests: tuple[dict[str, Any], ...], *, anchor: datetime, days: int) -> list[dict[str, Any]]:
    end_day = anchor.date()
    start_day = end_day - timedelta(days=days - 1)
    rows = {
        start_day + timedelta(days=offset): {"estimated_cost_usd": 0.0, "request_count": 0}
        for offset in range(days)
    }
    for item in manifests:
        timestamp = _manifest_timestamp(item)
        if timestamp is None or not (start_day <= timestamp.date() <= end_day):
            continue
        bucket = rows[timestamp.date()]
        bucket["estimated_cost_usd"] = round(bucket["estimated_cost_usd"] + _cost_estimate(item), 6)
        bucket["request_count"] += 1
    return [
        {
            "date": day.isoformat(),
            "estimated_cost_usd": values["estimated_cost_usd"],
            "request_count": values["request_count"],
        }
        for day, values in sorted(rows.items())
    ]


def _cost_drivers(manifests: tuple[dict[str, Any], ...], *, anchor: datetime, days: int) -> list[dict[str, Any]]:
    current_end = anchor + timedelta(seconds=1)
    current_start = current_end - timedelta(days=days)
    previous_start = current_start - timedelta(days=days)
    drivers: dict[str, dict[str, Any]] = {}
    for item in manifests:
        cost = _cost_estimate(item)
        if cost <= 0:
            continue
        timestamp = _manifest_timestamp(item)
        if timestamp is None or not (previous_start <= timestamp < current_end):
            continue
        lane = _lane_id(item)
        provider_class = _provider_class(item) or "unknown"
        adapter = item.get("adapter") or _provider_call(item).get("adapter") or "unknown"
        node = item.get("extras", {}).get("routing", {}).get("node_chosen") or item.get("extras", {}).get("node")
        key = f"{lane}:{provider_class}:{adapter}:{node or 'unknown'}"
        row = drivers.setdefault(
            key,
            {
                "id": key,
                "label": lane.replace("_", " "),
                "lane": lane,
                "provider_class": provider_class,
                "adapter": adapter,
                "node": node,
                "estimated_cost_usd": 0.0,
                "previous_estimated_cost_usd": 0.0,
                "request_count": 0,
                "billable_units": 0,
                "latest_at": None,
            },
        )
        if current_start <= timestamp < current_end:
            row["estimated_cost_usd"] = round(row["estimated_cost_usd"] + cost, 6)
            row["request_count"] += 1
            row["billable_units"] += _billable_units(item)
            row["latest_at"] = max(row["latest_at"] or timestamp.isoformat(), timestamp.isoformat())
        else:
            row["previous_estimated_cost_usd"] = round(row["previous_estimated_cost_usd"] + cost, 6)
    for row in drivers.values():
        row["trend"] = _trend_fields(row["estimated_cost_usd"], row["previous_estimated_cost_usd"])
    return sorted(
        drivers.values(),
        key=lambda row: (row["estimated_cost_usd"], row["previous_estimated_cost_usd"], row["request_count"]),
        reverse=True,
    )[:20]


def _context_health(manifests: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    contexts = [_context(item) for item in manifests]
    ratios = [_compression_ratio(context) for context in contexts]
    ratios = [ratio for ratio in ratios if ratio is not None]
    raw_tokens = [_numeric(context.get("raw_tokens")) for context in contexts]
    after_tokens = [_numeric(context.get("after_compression_tokens")) for context in contexts]
    quality_scores = [_numeric(context.get("quality_score")) for context in contexts]
    compressed_calls = sum(1 for context in contexts if _compression_applied(context))
    hard_reduction_calls = sum(1 for context in contexts if _hard_reduction(context))
    quality_issue_calls = sum(1 for context in contexts if _context_quality_issue(context))
    retrieved_chunks = int(sum(_numeric(context.get("retrieved_chunks")) or 0 for context in contexts))
    used_chunks = int(sum(_numeric(context.get("used_chunks")) or 0 for context in contexts))
    dropped_chunks = int(sum(_numeric(context.get("dropped_chunks")) or 0 for context in contexts))
    saved_tokens = int(sum(_numeric(context.get("compression_saved_tokens")) or 0 for context in contexts))
    budget_statuses = _counts(
        context.get("budget_pressure_status") if isinstance(context.get("budget_pressure_status"), str) else "unknown"
        for context in contexts
    )
    retrieval_modes = _counts(
        context.get("retrieval_mode") if isinstance(context.get("retrieval_mode"), str) else "unknown"
        for context in contexts
    )
    query_shapes = _counts(
        _query_shape(context)
        for context in contexts
    )
    memory_tiers = _counts(
        tier
        for context in contexts
        for tier in _memory_tiers_used(context)
    )
    effective_window_guarded = sum(1 for context in contexts if _effective_window_guarded(context))
    prompt_cache_ordered = sum(1 for context in contexts if isinstance(context.get("prompt_cache_order_version"), str))
    return {
        "window_days": 7,
        "request_count": len(manifests),
        "compressed_calls": compressed_calls,
        "compression_rate": _rate(compressed_calls, len(manifests)),
        "context_pressure_calls": sum(1 for context in contexts if _context_pressure(context)),
        "hard_reduction_calls": hard_reduction_calls,
        "avg_retained_ratio": _mean(ratios),
        "avg_reduction_ratio": round(1 - _mean(ratios), 6) if ratios else None,
        "avg_raw_tokens": _mean(raw_tokens),
        "avg_after_compression_tokens": _mean(after_tokens),
        "saved_tokens": saved_tokens,
        "retrieved_chunks": retrieved_chunks,
        "used_chunks": used_chunks,
        "dropped_chunks": dropped_chunks,
        "injection_rate": _rate(used_chunks, retrieved_chunks),
        "avg_quality_score": _mean(quality_scores),
        "quality_issue_calls": quality_issue_calls,
        "quality_statuses": _counts(
            context.get("quality_score_status") if isinstance(context.get("quality_score_status"), str) else "unknown"
            for context in contexts
        ),
        "stop_reasons": _counts(
            context.get("stop_reason") if isinstance(context.get("stop_reason"), str) else "unknown"
            for context in contexts
        ),
        "budget_pressure_statuses": budget_statuses,
        "retrieval_modes": retrieval_modes,
        "query_shapes": query_shapes,
        "memory_tiers_used": memory_tiers,
        "effective_window_guarded_calls": effective_window_guarded,
        "prompt_cache_ordered_calls": prompt_cache_ordered,
        "context_pack_contract": {
            "schema": "bloxguard.context_pack.v1",
            "query_understanding_required": True,
            "effective_window_enforced": True,
            "cache_order_versioned": True,
            "memory_tiers": ["working", "episodic", "semantic", "procedural"],
            "canonical_zones": [
                "tools",
                "system_contract",
                "current_task",
                "session_state",
                "bloxvault_evidence",
                "tool_state",
                "reasoning_reserve",
                "output_reserve",
                "safety_reserve",
            ],
        },
    }


def _query_shape(context: dict[str, Any]) -> str:
    query = context.get("query_understanding")
    if isinstance(query, dict):
        shape = query.get("shape") or query.get("query_shape")
        if isinstance(shape, str):
            return shape
    shape = context.get("query_shape")
    return shape if isinstance(shape, str) else "unknown"


def _memory_tiers_used(context: dict[str, Any]) -> tuple[str, ...]:
    value = context.get("memory_tiers_used")
    if isinstance(value, list):
        tiers = tuple(str(item) for item in value if item)
        return tiers or ("none",)
    return ("none",)


def _effective_window_guarded(context: dict[str, Any]) -> bool:
    effective = _numeric(context.get("candidate_effective_context_tokens"))
    marketed = _numeric(context.get("candidate_marketed_context_tokens"))
    if effective is None:
        return False
    return marketed is None or effective <= marketed


def _compression_applied(context: dict[str, Any]) -> bool:
    return bool(
        context.get("compression_steps")
        or (_numeric(context.get("compression_saved_tokens")) or 0) > 0
        or (_compression_ratio(context) is not None and (_compression_ratio(context) or 1) < 0.98)
    )


def _context_pressure(context: dict[str, Any]) -> bool:
    return _compression_applied(context) or (_numeric(context.get("dropped_chunks")) or 0) > 0


def _hard_reduction(context: dict[str, Any]) -> bool:
    ratio = _compression_ratio(context)
    return bool((ratio is not None and ratio <= 0.25) or (_numeric(context.get("dropped_chunks")) or 0) > 0)


def _context_quality_issue(context: dict[str, Any]) -> bool:
    status = context.get("quality_score_status")
    stop_reason = context.get("stop_reason")
    score = _numeric(context.get("quality_score"))
    return bool(
        status in {"retrieval_not_connected", "retrieved_but_privacy_denied", "low_quality", "failed"}
        or stop_reason in {"unanswerable", "retrieval_not_connected"}
        or (score is not None and score < 0.55)
    )


def _compression_ratio(context: dict[str, Any]) -> float | None:
    ratio = _numeric(context.get("compression_ratio"))
    if ratio is not None:
        return ratio
    raw_tokens = _numeric(context.get("raw_tokens"))
    after_tokens = _numeric(context.get("after_compression_tokens"))
    if raw_tokens and after_tokens is not None:
        return round(after_tokens / raw_tokens, 6)
    return None


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


def _ledger(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("ledger_ref") or {}


def _cost_block(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("cost") or {}
    return value if isinstance(value, dict) else {}


def _context(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("context") or {}


def _manifest_identity(item: dict[str, Any]) -> str | None:
    value = item.get("manifest_id") or item.get("id") or item.get("request_id")
    return value if isinstance(value, str) and value else None


def _caller_agent(item: dict[str, Any]) -> str:
    caller = item.get("caller") or {}
    value = (
        caller.get("agent")
        or caller.get("agent_id")
        or caller.get("name")
        or item.get("agent")
        or item.get("owner_id")
        or "unknown"
    )
    return value if isinstance(value, str) and value else "unknown"


def _outcome_status(item: dict[str, Any]) -> str:
    outcome = item.get("outcome") or {}
    value = outcome.get("status") or item.get("status") or "unknown"
    return value if isinstance(value, str) and value else "unknown"


def _provider_model(item: dict[str, Any]) -> str | None:
    provider_call = _provider_call(item)
    decision = item.get("lane_decision") or {}
    node_model = item.get("node_model") or {}
    value = provider_call.get("model") or decision.get("selected_model") or node_model.get("model") or item.get("model")
    return value if isinstance(value, str) and value else None


def _token_accounting(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("token_accounting") or {}
    return value if isinstance(value, dict) else {}


def _duration_ms(item: dict[str, Any]) -> int | None:
    provider_call = _provider_call(item)
    direct = _numeric(provider_call.get("latency_ms")) or _numeric(provider_call.get("duration_ms"))
    if direct is not None:
        return int(direct)
    started = _parse_timestamp(item.get("started_at") or item.get("created_at") or item.get("at"))
    completed = _parse_timestamp(item.get("completed_at") or item.get("finished_at"))
    if started is None or completed is None:
        return None
    return max(0, int((completed - started).total_seconds() * 1000))


def _parse_timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _bloxguard_call_log_item(item: dict[str, Any]) -> dict[str, Any]:
    context = _context(item)
    tokens = _token_accounting(item)
    realised = _realised(item)
    debug_context = _context_debug_payload(item)
    return {
        "id": _manifest_identity(item),
        "request_id": item.get("request_id"),
        "job_id": item.get("job_id"),
        "session_id": item.get("session_id"),
        "created_at": item.get("created_at") or item.get("at"),
        "completed_at": item.get("completed_at"),
        "duration_ms": _duration_ms(item),
        "agent": _caller_agent(item),
        "lane": _lane_id(item),
        "candidate": _candidate_id(item),
        "provider_class": _provider_class(item) or "unknown",
        "model": _provider_model(item),
        "status": _outcome_status(item),
        "error_class": _classified_error(item),
        "fallback_used": _fallback_used(item),
        "cost_usd": _cost_estimate(item),
        "tokens": {
            "input_est": tokens.get("input_tokens_est") or realised.get("tokens_in_total"),
            "output_est": tokens.get("output_tokens_est") or realised.get("tokens_out"),
            "input_budget_limit": tokens.get("input_budget_limit")
            or (item.get("extras", {}).get("routing", {}) if isinstance(item.get("extras"), dict) else {}).get(
                "input_budget_limit"
            ),
        },
        "context": {
            "raw_tokens": context.get("raw_tokens"),
            "after_compression_tokens": context.get("after_compression_tokens"),
            "compression_ratio": _compression_ratio(context),
            "compression_saved_tokens": context.get("compression_saved_tokens"),
            "retrieved_chunks": context.get("retrieved_chunks"),
            "used_chunks": context.get("used_chunks"),
            "dropped_chunks": context.get("dropped_chunks"),
            "quality_score": context.get("quality_score"),
            "quality_score_status": context.get("quality_score_status"),
            "stop_reason": context.get("stop_reason"),
            "debug_captured": debug_context["captured"],
        },
        "redaction": item.get("redaction") or {},
    }


def _context_debug_payload(item: dict[str, Any]) -> dict[str, Any]:
    context = _context(item)
    debug = _first_dict(
        item.get("debug_context"),
        item.get("context_debug"),
        context.get("debug"),
        context.get("snapshots"),
        context.get("debug_snapshots"),
    )
    input_value = _first_context_snapshot(
        item,
        context,
        debug,
        (
            ("context_input",),
            ("input_context",),
            ("raw_context",),
            ("raw_prompt",),
            ("prompt_before_context",),
            ("context_before_compression",),
            ("context_before_build",),
            ("input",),
        ),
    )
    compressed_value = _first_context_snapshot(
        item,
        context,
        debug,
        (
            ("context_compressed",),
            ("compressed_context",),
            ("context_after_compression",),
            ("after_compression_context",),
            ("compressed",),
        ),
    )
    injected_value = _first_context_snapshot(
        item,
        context,
        debug,
        (
            ("context_injected",),
            ("injected_context",),
            ("llm_context",),
            ("final_context",),
            ("context_after_build",),
            ("prompt_to_llm",),
            ("injected",),
        ),
    )
    redaction = item.get("redaction") or {}
    notes = []
    if not any((input_value, compressed_value, injected_value)):
        if redaction.get("raw_prompt_stored") is False:
            notes.append("Raw prompt/context was not stored for this call.")
        notes.append("Enable Debug capture in BloxGuard Config and make the middleware emit input, compressed, and injected context snapshots.")
    return {
        "captured": bool(input_value or compressed_value or injected_value),
        "redaction": redaction,
        "input": _snapshot_cell(input_value),
        "compressed": _snapshot_cell(compressed_value),
        "injected": _snapshot_cell(injected_value),
        "notes": notes,
    }


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _first_context_snapshot(
    item: dict[str, Any],
    context: dict[str, Any],
    debug: dict[str, Any],
    paths: tuple[tuple[str, ...], ...],
) -> Any:
    for root in (debug, context, item):
        for path in paths:
            value = _nested_value(root, path)
            if value is not None:
                return value
    return None


def _nested_value(root: Any, path: tuple[str, ...]) -> Any:
    current = root
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _snapshot_cell(value: Any) -> dict[str, Any]:
    if value is None:
        return {"available": False, "text": None}
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, indent=2, ensure_ascii=False)
    return {
        "available": True,
        "text": text,
        "chars": len(text),
    }


def _classified_error(item: dict[str, Any]) -> str | None:
    return item.get("outcome", {}).get("classified_error") or item.get("outcome", {}).get("error_class")


def _fallback_used(item: dict[str, Any]) -> bool:
    decision = item.get("lane_decision") or {}
    return bool(decision.get("fallback_used") or item.get("outcome", {}).get("fallback_taken"))


def _cost_estimate(item: dict[str, Any]) -> float:
    cost = _cost_block(item)
    if "estimated_cost_usd" in cost:
        return float(cost.get("estimated_cost_usd") or 0.0)
    ledger = _ledger(item)
    if "estimated_cost_usd" in ledger:
        return float(ledger.get("estimated_cost_usd") or 0.0)
    return float(_realised(item).get("cost_usd", 0.0) or 0.0)


def _provider_class(item: dict[str, Any]) -> str | None:
    node_model = item.get("node_model") or {}
    return item.get("provider_class") or _cost_block(item).get("provider_class") or _ledger(item).get("provider_class") or node_model.get("provider")


def _billable_units(item: dict[str, Any]) -> int:
    cost = _cost_block(item)
    if "billable_units" in cost:
        return int(cost.get("billable_units") or 0)
    ledger = _ledger(item)
    return int((ledger.get("oauth_units_est") or 0) + (ledger.get("image_units_est") or 0))


def _provider_call(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("provider_call") or {}
    return value if isinstance(value, dict) else {}


def _lane_id(item: dict[str, Any]) -> str:
    intent = item.get("intent") or {}
    extras = item.get("extras") or {}
    decision = item.get("lane_decision") or {}
    value = (
        intent.get("lane")
        or decision.get("selected_lane")
        or decision.get("routed_lane")
        or _first_string(decision.get("routed_lane_ids"))
        or _first_string(decision.get("eligible_lanes"))
        or extras.get("capability")
        or item.get("lane")
        or item.get("adapter")
        or "unknown"
    )
    return value if isinstance(value, str) and value else "unknown"


def _candidate_id(item: dict[str, Any]) -> str:
    decision = item.get("lane_decision") or {}
    provider_call = _provider_call(item)
    value = (
        decision.get("selected_candidate")
        or decision.get("candidate")
        or provider_call.get("candidate")
        or item.get("candidate")
        or "unknown"
    )
    return value if isinstance(value, str) and value else "unknown"


def _bloxguard_config_health(config: dict[str, Any]) -> dict[str, Any]:
    lanes = config.get("lanes") if isinstance(config.get("lanes"), list) else []
    nodes = {
        str(value).strip()
        for lane in lanes
        if isinstance(lane, dict)
        for value in _lane_candidate_nodes(lane)
        if str(value).strip()
    }
    node_health = {node: _node_health_probe(node, config) for node in sorted(nodes)}
    candidate_health: dict[str, dict[str, Any]] = {}
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        lane_id = str(lane.get("id") or "")
        for candidate in lane.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(candidate.get("id") or "")
            if not lane_id or not candidate_id:
                continue
            key = candidate_id if candidate_id.startswith(f"{lane_id}.") else f"{lane_id}.{candidate_id}"
            node = str(candidate.get("node") or lane.get("node") or "").strip()
            provider_class = str(candidate.get("provider_class") or lane.get("provider_class") or "").strip()
            model = str(candidate.get("model") or lane.get("model") or "").strip()
            candidate_health[key] = _candidate_health_row(
                candidate=candidate,
                node=node,
                provider_class=provider_class,
                model=model,
                node_health=node_health.get(node),
                config=config,
            )
    return {"node_health": node_health, "candidate_health": candidate_health}


def _lane_candidate_nodes(lane: dict[str, Any]) -> list[str]:
    values = [str(lane.get("node") or "")]
    for candidate in lane.get("candidates") or []:
        if isinstance(candidate, dict):
            values.append(str(candidate.get("node") or lane.get("node") or ""))
    return values


def _node_health_probe(node: str, config: dict[str, Any]) -> dict[str, Any]:
    normalized = node.strip().lower()
    if normalized == "cortex":
        return {
            "status": "retired",
            "routeable": False,
            "models": [],
            "reason": "cortex_retired",
        }
    if normalized in _BLOXGUARD_AUTO_ROUTE_VALUES:
        return {
            "status": "auto",
            "routeable": True,
            "models": [],
            "reason": "broker_selects_runtime_node",
        }
    if normalized in {"cloud", "openai", "anthropic", "openrouter", "gemini", "xai"}:
        cloud_enabled = bool(_object(config.get("route_policy")).get("cloud_enabled"))
        return {
            "status": "ok" if cloud_enabled else "policy_disabled",
            "routeable": cloud_enabled,
            "models": [],
            "reason": "cloud_enabled" if cloud_enabled else "cloud_disabled_by_policy",
        }
    endpoint = _BLOXGUARD_OLLAMA_ENDPOINTS.get(normalized)
    if not endpoint:
        return {
            "status": "unverified",
            "routeable": True,
            "models": [],
            "reason": "ollama_probe_not_configured",
        }
    try:
        with urllib.request.urlopen(f"{endpoint.rstrip('/')}/api/tags", timeout=_BLOXGUARD_OLLAMA_PROBE_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "status": "unverified",
            "routeable": True,
            "models": [],
            "reason": f"ollama_probe_failed:{type(exc).__name__}",
        }
    models = [
        str(item.get("name") or item.get("model") or "")
        for item in payload.get("models", [])
        if isinstance(item, dict) and (item.get("name") or item.get("model"))
    ]
    return {"status": "ok", "routeable": True, "models": models, "reason": "ollama_tags_ok"}


def _candidate_health_row(
    *,
    candidate: dict[str, Any],
    node: str,
    provider_class: str,
    model: str,
    node_health: dict[str, Any] | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    if candidate.get("enabled") is False:
        return {"status": "disabled", "routeable": False, "reason": "candidate_disabled"}
    provider = provider_class or "local"
    normalized_node = node.strip().lower()
    if (
        provider in {"cloud_oauth", "openai", "anthropic", "openrouter", "gemini", "xai", "oauth"}
        or normalized_node == "cloud"
    ):
        cloud_enabled = bool(_object(config.get("route_policy")).get("cloud_enabled"))
        return {
            "status": "ok" if cloud_enabled else "policy_disabled",
            "routeable": cloud_enabled,
            "reason": "cloud_enabled" if cloud_enabled else "cloud_disabled_by_policy",
        }
    if not node_health:
        return {"status": "unknown", "routeable": False, "reason": "node_health_missing"}
    if node_health.get("status") == "auto":
        return {"status": "auto", "routeable": True, "reason": "broker_selects_runtime_node"}
    if node_health.get("status") == "unverified":
        return {
            "status": "unverified",
            "routeable": True,
            "reason": node_health.get("reason") or "model_probe_unverified",
        }
    if node_health.get("status") != "ok" or not node_health.get("routeable"):
        return {"status": "down", "routeable": False, "reason": node_health.get("reason") or "node_not_routeable"}
    models = set(node_health.get("models") or [])
    if model and model not in {"auto", "auto_heavy", "auto_balanced", "auto_planning"} and model not in models:
        return {"status": "model_missing", "routeable": False, "reason": f"model_not_loaded:{model}"}
    return {"status": "ok", "routeable": True, "reason": "health_gated_model_present"}


_EDITABLE_CANDIDATE_FIELDS = {
    "enabled",
    "node",
    "provider_class",
    "model",
    "max_context_tokens",
    "requests_per_minute",
    "tokens_per_minute",
    "weight",
    "max_privacy_tier",
    "profile",
    "envelope_confidence",
}

_EDITABLE_LANE_FIELDS = {
    "label",
    "enabled",
    "node",
    "provider_class",
    "model",
    "max_context_tokens",
    "max_concurrency",
    "requests_per_minute",
    "tokens_per_minute",
    "weight",
    "max_privacy_tier",
    "fallback_chain",
}

_EDITABLE_REDACTION_FIELDS = {
    "enabled",
    "strategy",
    "fail_closed",
    "store_maps",
    "map_path",
}

_VALID_BLOXGUARD_MODES = {"observe_only", "observe_then_route", "route_enforced", "disabled"}
_VALID_BLOXGUARD_BYPASS_MODES = {"degraded_passthrough", "fail_closed", "local_only", "cloud_only"}
_VALID_BLOXGUARD_COMPRESSION_MODES = {"auto", "adaptive", "off", "low", "medium", "high"}
_BLOXGUARD_AUTO_ROUTE_VALUES = {"auto", "broker", "router", "auto_node"}
_BLOXGUARD_OLLAMA_PROBE_TIMEOUT_SECONDS = float(os.environ.get("BLOXGUARD_OLLAMA_PROBE_TIMEOUT_SECONDS", "0.75"))

_BLOXGUARD_OLLAMA_ENDPOINTS = {
    "apex": os.environ.get("BLOXGUARD_APEX_OLLAMA", "http://192.168.111.210:11434"),
    "neuroforge": os.environ.get("BLOXGUARD_NEUROFORGE_OLLAMA", "http://192.168.111.200:11434"),
    "outpost": os.environ.get("BLOXGUARD_OUTPOST_OLLAMA", "http://192.168.111.160:11434"),
}


def _bloxguard_module_page_contract() -> dict[str, Any]:
    try:
        module_page_contract = _load_bloxguard_symbol("blox_guard.module_page", "module_page_contract")
        contract = module_page_contract()
        if isinstance(contract, dict):
            return contract
    except Exception as exc:  # noqa: BLE001 - dashboard should degrade visibly, not lose the page.
        return {
            **_fallback_bloxguard_module_contract(),
            "source": "cockpit_fallback",
            "warning": f"bloxguard_module_contract_unavailable:{type(exc).__name__}",
        }
    return _fallback_bloxguard_module_contract()


def _validate_bloxguard_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    try:
        load_config = _load_bloxguard_symbol("blox_guard.config", "load_config")
    except Exception as exc:  # noqa: BLE001 - keep read-only pages usable when package path is absent.
        return {"ok": True, "source": "cockpit_compat", "warning": f"bloxguard_validator_unavailable:{type(exc).__name__}"}
    try:
        load_config(config)
        return {"ok": True, "source": "blox_guard.config.load_config", "compatibility_projection": False}
    except Exception as first_exc:  # noqa: BLE001 - legacy live configs may still need alias projection.
        projected = _bloxguard_validation_projection(config)
        try:
            load_config(projected)
        except Exception as second_exc:  # noqa: BLE001
            return {
                "ok": False,
                "source": "blox_guard.config.load_config",
                "error": str(second_exc),
                "original_error": str(first_exc),
            }
        return {
            "ok": True,
            "source": "blox_guard.config.load_config",
            "compatibility_projection": True,
            "warning": str(first_exc),
        }


def _load_bloxguard_symbol(module_name: str, symbol_name: str) -> Any:
    for root in _bloxguard_package_roots():
        root_text = str(root)
        if root.exists() and root_text not in sys.path:
            sys.path.insert(0, root_text)
    module = __import__(module_name, fromlist=[symbol_name])
    return getattr(module, symbol_name)


def _bloxguard_package_roots() -> tuple[Path, ...]:
    env_root = os.environ.get("BLOXGUARD_PACKAGE_ROOT") or os.environ.get("COCKPIT_BLOXGUARD_PACKAGE_ROOT")
    roots = []
    if env_root:
        roots.append(Path(env_root).expanduser())
    roots.extend(
        [
            Path("/opt/agentic-blox"),
            Path.cwd().parent / "agentic-blox",
            Path(__file__).resolve().parents[4] / "agentic-blox",
        ]
    )
    return tuple(roots)


def _bloxguard_validation_projection(config: dict[str, Any]) -> dict[str, Any]:
    projected = json.loads(json.dumps(config))
    mode_aliases = {"enforced": "route_enforced", "routing": "observe_then_route"}
    compression_aliases = {"adaptive": "auto"}
    if projected.get("mode") in mode_aliases:
        projected["mode"] = mode_aliases[str(projected.get("mode"))]
    if projected.get("compression_mode") in compression_aliases:
        projected["compression_mode"] = compression_aliases[str(projected.get("compression_mode"))]
    privacy = _ensure_plain_dict(projected, "privacy")
    privacy.setdefault("untagged_document_default", "private")
    privacy.setdefault("path_sensitive_default", "secret")
    privacy.setdefault("max_default_request_tier", "internal")
    token = _ensure_plain_dict(projected, "token_estimation")
    token.setdefault("strategy", "model_specific_when_available_else_conservative")
    token.setdefault("safety_margin_percent", 15)
    rag = _ensure_plain_dict(projected, "agentic_rag")
    rag.setdefault("max_loops", 3)
    rag.setdefault("stop_confidence", 0.82)
    rag.setdefault("min_source_novelty_ratio", 0.20)
    rag.setdefault("min_query_novelty_ratio", 0.15)
    lanes = projected.get("lanes") if isinstance(projected.get("lanes"), list) else []
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        lane.setdefault("node", "auto")
        lane.setdefault("provider_class", "local")
        lane.setdefault("max_concurrency", 1)
        lane.setdefault("requests_per_minute", 0)
        lane.setdefault("tokens_per_minute", 0)
        lane.setdefault("max_context_tokens", 0)
        lane.setdefault("fallback_chain", [])
        for candidate in lane.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            candidate.setdefault("provider_class", lane.get("provider_class", "local"))
            candidate.setdefault("max_concurrency", lane.get("max_concurrency", 1))
            candidate.setdefault("requests_per_minute", lane.get("requests_per_minute", 0))
            candidate.setdefault("tokens_per_minute", lane.get("tokens_per_minute", 0))
            candidate.setdefault("max_context_tokens", candidate.get("max_context_tokens", lane.get("max_context_tokens", 0)))
    return projected


def _ensure_plain_dict(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    if not isinstance(value, dict):
        value = {}
        config[key] = value
    return value


def _fallback_bloxguard_module_contract() -> dict[str, Any]:
    return {
        "schema_version": "bloxguard.module_page.v1",
        "module": "BloxGuard",
        "route": "/bloxguard/",
        "owner": "BloxGuard",
        "frame_owner": "BloxBoard",
        "rule": "BloxBoard owns the frame; BloxGuard owns the module page content, data contract, config validation, and audit semantics.",
        "tabs": [
            {"id": "overview", "label": "Overview", "submodules": ["BG-Operations", "BG-Manifest", "BG-Cost"], "endpoint": "/api/bloxguard/summary", "owns_config": False},
            {"id": "costs", "label": "Costs", "submodules": ["BG-Cost"], "endpoint": "/api/bloxguard/config", "owns_config": True},
            {"id": "context", "label": "Context", "submodules": ["BG-Context", "BG-CONTEXT-001", "BG-CONTEXT-002", "BG-CONTEXT-003", "BG-CONTEXT-004", "BG-CONTEXT-005", "BG-CONTEXT-006", "BG-Debug"], "endpoint": "/api/bloxguard/summary", "owns_config": False},
            {"id": "lanes", "label": "Lanes", "submodules": ["BG-Router", "BG-Providers", "BG-Policy"], "endpoint": "/api/bloxguard/config", "owns_config": True},
            {"id": "candidates", "label": "Candidates", "submodules": ["BG-Router", "BG-Providers", "BG-Policy"], "endpoint": "/api/bloxguard/config", "owns_config": True},
            {"id": "config", "label": "Config", "submodules": ["BG-Operations", "BG-Policy", "BG-Redaction", "BG-Manifest"], "endpoint": "/api/bloxguard/config", "owns_config": True},
            {"id": "logs", "label": "Logs", "submodules": ["BG-Manifest", "BG-Debug"], "endpoint": "/api/bloxguard/logs", "owns_config": False},
        ],
        "config_controls": [],
        "admin_actions": [],
        "guarantees": [
            "No module page config write bypasses BloxGuard validation.",
            "Risky config writes require an audit event and visible confirmation.",
            "Manifest and redaction map internals are not exposed as raw secret/private payloads.",
            "BloxBoard shell naming and navigation constraints are respected.",
        ],
    }

_BLOXGUARD_PROVIDER_SECRETS = (
    {
        "id": "openai",
        "label": "OpenAI",
        "env_var": "OPENAI_API_KEY",
        "aliases": ("OPENAI_API_KEY",),
    },
    {
        "id": "anthropic",
        "label": "Anthropic",
        "env_var": "ANTHROPIC_API_KEY",
        "aliases": ("ANTHROPIC_API_KEY",),
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "env_var": "OPENROUTER_API_KEY",
        "aliases": ("OPENROUTER_API_KEY",),
    },
    {
        "id": "gemini",
        "label": "Google Gemini",
        "env_var": "GEMINI_API_KEY",
        "aliases": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    },
    {
        "id": "xai",
        "label": "xAI",
        "env_var": "XAI_API_KEY",
        "aliases": ("XAI_API_KEY",),
    },
    {
        "id": "ollama_cloud",
        "label": "Ollama Cloud",
        "env_var": "OLLAMA_API_KEY",
        "aliases": ("OLLAMA_API_KEY",),
    },
    {
        "id": "deepgram",
        "label": "Deepgram",
        "env_var": "DEEPGRAM_API_KEY",
        "aliases": ("DEEPGRAM_API_KEY",),
    },
    {
        "id": "vapi",
        "label": "Vapi",
        "env_var": "VAPI_API_KEY",
        "aliases": ("VAPI_API_KEY",),
    },
    {
        "id": "brave",
        "label": "Brave Search",
        "env_var": "BRAVE_API_KEY",
        "aliases": ("BRAVE_API_KEY",),
    },
    {
        "id": "x",
        "label": "X API",
        "env_var": "X_BEARER_TOKEN",
        "aliases": ("X_BEARER_TOKEN",),
    },
)


def _candidate_config_matches(
    lane: dict[str, Any],
    candidate: dict[str, Any],
    candidate_key: str,
) -> bool:
    lane_id = lane.get("id")
    candidate_id = candidate.get("id")
    keys = {candidate_id}
    if isinstance(lane_id, str) and isinstance(candidate_id, str):
        keys.add(f"{lane_id}.{candidate_id}")
    return candidate_key in keys


def _ensure_dict(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    if not isinstance(value, dict):
        value = {}
        config[key] = value
    return value


def _changed_top_level_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    keys = set(before) | set(after)
    return sorted(key for key in keys if before.get(key) != after.get(key))


def _redact_config_audit_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in ("secret", "token", "key", "password", "credential", "value")):
                redacted[key] = "<redacted>" if item not in (None, "") else item
            else:
                redacted[key] = _redact_config_audit_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_config_audit_payload(item) for item in value]
    return value


def _validate_choice(field: str, value: Any, valid: set[str]) -> str:
    selected = str(value)
    if selected not in valid:
        raise ConductorReadError(f"invalid_{field}:{selected}")
    return selected


def _validated_pricing_model(model_name: str, values: dict[str, Any]) -> dict[str, float]:
    return {
        "input_usd_per_1m": _non_negative_float(
            values.get("input_usd_per_1m"),
            f"{model_name}.input_usd_per_1m",
        ),
        "cached_input_usd_per_1m": _non_negative_float(
            values.get("cached_input_usd_per_1m"),
            f"{model_name}.cached_input_usd_per_1m",
        ),
        "output_usd_per_1m": _non_negative_float(
            values.get("output_usd_per_1m"),
            f"{model_name}.output_usd_per_1m",
        ),
    }


def _non_negative_float(value: Any, field: str) -> float:
    if value is None:
        return 0.0
    parsed = float(value)
    if parsed < 0:
        raise ConductorReadError(f"invalid_pricing_value:{field}")
    return parsed


def _provider_secret_definition(provider_id: str) -> dict[str, Any]:
    for provider in _BLOXGUARD_PROVIDER_SECRETS:
        if provider["id"] == provider_id:
            return provider
    raise ConductorReadError(f"bloxguard_provider_not_found:{provider_id}")


def _provider_secret_row(provider: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    aliases = tuple(str(item) for item in provider["aliases"])
    configured_env_var = next((env_var for env_var in aliases if values.get(env_var)), None)
    return {
        "id": provider["id"],
        "label": provider["label"],
        "env_var": configured_env_var or provider["env_var"],
        "accepted_env_vars": list(aliases),
        "configured": configured_env_var is not None,
    }


def _parse_env_file(raw: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in raw.splitlines():
        parsed = _parse_env_line(line)
        if parsed is not None:
            key, value = parsed
            values[key] = value
    return values


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped.removeprefix("export ").strip()
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key or not key.replace("_", "").isalnum():
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def _update_env_secret(
    raw: str,
    *,
    env_var: str,
    aliases: tuple[str, ...],
    value: str | None,
    clear: bool,
) -> str:
    clean_value = (value or "").strip()
    if not clear and not clean_value:
        raise ConductorReadError("secret_value_required")
    alias_set = set(aliases)
    lines: list[str] = []
    replaced = False
    for line in raw.splitlines():
        parsed = _parse_env_line(line)
        if parsed is None or parsed[0] not in alias_set:
            lines.append(line)
            continue
        if clear:
            continue
        if not replaced:
            lines.append(f"{env_var}={_quote_env_value(clean_value)}")
            replaced = True
    if not clear and not replaced:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{env_var}={_quote_env_value(clean_value)}")
    return "\n".join(lines).rstrip() + "\n"


def _quote_env_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _first_string(value: Any) -> str | None:
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, str) and item), None)
    return None


def _context_quality(report: dict[str, Any]) -> dict[str, Any]:
    retrieval = _object(report.get("retrieval"))
    sources = retrieval.get("sources")
    source_rows = sources if isinstance(sources, list) else []
    source_paths = [
        path
        for path in (_source_path(row) for row in source_rows)
        if path
    ]
    unique_sources = sorted(set(source_paths))
    chunks_offered = _number(retrieval.get("chunks_offered"))
    chunks_used = _number(retrieval.get("chunks_used"))
    final_count = _number(retrieval.get("final_count"))
    seed_count = _number(retrieval.get("seed_count"))
    neighbour_count = _number(retrieval.get("neighbour_count"))
    return {
        "mode": retrieval.get("mode") or "unknown",
        "memory_mcp_version": retrieval.get("memory_mcp_version"),
        "latency_ms": retrieval.get("latency_ms"),
        "top_score": retrieval.get("top_score"),
        "chunks_offered": chunks_offered,
        "chunks_used": chunks_used,
        "final_count": final_count,
        "seed_count": seed_count,
        "neighbour_count": neighbour_count,
        "coverage_ratio": round(chunks_used / chunks_offered, 6)
        if chunks_offered and chunks_used is not None
        else None,
        "unique_source_count": len(unique_sources),
        "source_count": len(source_paths),
        "sources": unique_sources[:20],
    }


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, int | float) else None


def _numeric(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _mean(values: list[float | None]) -> float | None:
    numeric = [value for value in values if value is not None]
    if not numeric:
        return None
    return round(sum(numeric) / len(numeric), 6)


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return round(float(numerator) / float(denominator), 6)


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = value if isinstance(value, str) and value else "unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def _source_path(row: Any) -> str | None:
    if isinstance(row, str):
        return row
    if not isinstance(row, dict):
        return None
    path = row.get("path") or row.get("source") or row.get("uri") or row.get("vault_path")
    return path if isinstance(path, str) and path else None
