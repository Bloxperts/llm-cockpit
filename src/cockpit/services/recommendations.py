"""Explainable recommendation scoring for benchmark profiles.

The scorer is deliberately transparent: every score is derived from facts
already present in the dashboard snapshot, and missing facts reduce confidence
instead of being guessed.
"""

from __future__ import annotations

from typing import Any

USE_CASES = ("chat", "code", "large_context", "multi_gpu")


def _clamp_score(value: float) -> int:
    return max(0, min(100, int(round(value))))


def _norm(value: float | None, best: float) -> float:
    if value is None or best <= 0:
        return 0.0
    return max(0.0, min(1.0, value / best))


def _inverse_seconds(value: float | None, *, best: float, worst: float) -> float:
    if value is None:
        return 0.0
    if value <= best:
        return 1.0
    if value >= worst:
        return 0.0
    return 1.0 - ((value - best) / (worst - best))


def _has_capability(metadata: dict[str, Any], *needles: str) -> bool:
    caps = [str(c).lower() for c in metadata.get("capabilities") or []]
    return any(any(needle in cap for cap in caps) for needle in needles)


def _positive_gpu_deltas(metrics: dict[str, Any]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for key, value in (metrics.get("gpu_layout_diff") or {}).items():
        try:
            mb = int(value)
        except (TypeError, ValueError):
            continue
        if mb > 0:
            out.append((str(key), mb))
    return out


def _cap_confidence(confidence: str, cap: str) -> str:
    rank = {"insufficient": 0, "low": 1, "medium": 2, "high": 3}
    by_rank = {value: key for key, value in rank.items()}
    return by_rank[min(rank.get(confidence, 0), rank.get(cap, 0))]


def _confidence(metrics: dict[str, Any], warnings: list[str], *, needs_gpu: bool = False) -> str:
    facts = sum(
        1
        for key in ("throughput_tps", "warm_load_seconds", "cold_load_seconds", "max_ctx_observed")
        if metrics.get(key) is not None
    )
    notes = str(metrics.get("notes") or "").lower()
    if "error" in notes or "failed" in notes:
        return "insufficient"
    if metrics.get("profile_status") in {"failed", "skipped", "incomplete"}:
        return "insufficient"
    if metrics.get("profile_status") == "partial" or metrics.get("data_quality") == "partial":
        return "low"
    if needs_gpu and not metrics.get("gpu_layout_diff"):
        return "low"
    if facts >= 4 and not warnings:
        return "high"
    if facts >= 3:
        confidence = "medium"
    elif facts >= 1:
        confidence = "low"
    else:
        return "insufficient"
    age_days = metrics.get("age_days")
    if isinstance(age_days, int | float):
        if age_days >= 90:
            return "insufficient"
        if age_days >= 30:
            confidence = _cap_confidence(confidence, "low")
        elif age_days >= 14:
            confidence = _cap_confidence(confidence, "medium")
    drift_status = metrics.get("drift_status")
    if drift_status == "warning":
        confidence = _cap_confidence(confidence, "low")
    elif drift_status == "unknown" and facts >= 1:
        confidence = _cap_confidence(confidence, "medium")
    trend_status = metrics.get("trend_status")
    if trend_status == "warning":
        confidence = _cap_confidence(confidence, "low")
    elif trend_status == "unknown" and facts >= 1:
        confidence = _cap_confidence(confidence, "medium")
    return confidence


def score_recommendations(
    *,
    model_name: str,
    tag: str | None,
    metadata: dict[str, Any],
    metrics: dict[str, Any],
    size_bytes: int | None = None,
) -> list[dict[str, Any]]:
    """Return one scored recommendation per supported use case.

    Scores are comparable within a use case. Confidence describes whether the
    score is backed by enough measured data to trust it.
    """

    del model_name
    profile = metrics.get("benchmark_profile") or metrics.get("placement_tested") or "on_demand"
    tps = metrics.get("throughput_tps")
    warm = metrics.get("warm_load_seconds")
    cold = metrics.get("cold_load_seconds")
    ctx = metrics.get("max_ctx_observed")
    tag_value = tag or ""
    gpu_deltas = _positive_gpu_deltas(metrics)
    arch_ctx = metadata.get("architecture_context_length")
    notes = metrics.get("notes")

    shared_warnings: list[str] = []
    if tps is None:
        shared_warnings.append("tokens/s not measured")
    if warm is None:
        shared_warnings.append("warm-load time not measured")
    if cold is None:
        shared_warnings.append("cold-load time not measured")
    if notes:
        shared_warnings.append(str(notes))
    profile_status = metrics.get("profile_status")
    if profile_status in {"failed", "skipped", "partial", "incomplete"}:
        shared_warnings.append(f"profile status is {profile_status}; retest this profile")
    age_days = metrics.get("age_days")
    if isinstance(age_days, int | float):
        if age_days >= 30:
            shared_warnings.append(f"benchmark is old ({age_days:.0f} days); retest before trusting decisions")
        elif age_days >= 14:
            shared_warnings.append(f"benchmark is stale ({age_days:.0f} days)")
    trend_status = metrics.get("trend_status")
    if trend_status == "warning":
        shared_warnings.extend(str(signal) for signal in metrics.get("trend_signals") or [])
    elif trend_status == "unknown":
        shared_warnings.append("not enough benchmark history for trend confidence")
    drift_status = metrics.get("drift_status")
    if drift_status == "warning":
        shared_warnings.extend(str(signal) for signal in metrics.get("drift_signals") or [])
    elif drift_status == "unknown":
        shared_warnings.append("not enough benchmark history for drift confidence")

    rows: list[dict[str, Any]] = []

    chat_reasons = []
    chat_score = 45 * _norm(tps, 45) + 25 * _inverse_seconds(warm, best=1.0, worst=8.0)
    chat_score += 10 * _inverse_seconds(cold, best=4.0, worst=30.0)
    if tag_value in {"chat", "both"}:
        chat_score += 15
        chat_reasons.append(f"model tag is {tag_value}")
    if profile.startswith("gpu") or profile == "on_demand":
        chat_score += 5
        chat_reasons.append(f"{profile} profile is suitable for interactive use")
    if tps is not None:
        chat_reasons.append(f"{tps:.1f} tokens/s measured")
    if warm is not None:
        chat_reasons.append(f"{warm:.1f}s warm load measured")
    rows.append(
        _row("chat", chat_score, chat_reasons, shared_warnings, metrics)
    )

    code_reasons = []
    code_score = 35 * _norm(tps, 35) + 20 * _norm(ctx, 32768)
    code_score += 10 * _inverse_seconds(warm, best=1.5, worst=10.0)
    if tag_value in {"code", "both"}:
        code_score += 20
        code_reasons.append(f"model tag is {tag_value}")
    if _has_capability(metadata, "tool", "code", "completion"):
        code_score += 10
        code_reasons.append("metadata advertises code/tool capability")
    if ctx is not None:
        code_reasons.append(f"{int(ctx):,} token context observed")
    rows.append(
        _row("code", code_score, code_reasons, shared_warnings, metrics)
    )

    large_reasons = []
    large_warnings = list(shared_warnings)
    large_score = 60 * _norm(ctx, 65536) + 15 * _norm(tps, 25)
    if arch_ctx:
        large_score += 10 * _norm(arch_ctx, 65536)
        large_reasons.append(f"architecture context metadata: {int(arch_ctx):,}")
    if profile == "multi_gpu":
        large_score += 10
        large_reasons.append("multi-GPU profile can carry larger memory pressure")
    elif profile.startswith("gpu"):
        large_score += 5
        large_reasons.append(f"{profile} profile has a pinned GPU measurement")
    if ctx is None:
        large_warnings.append("max context was not probed")
    else:
        large_reasons.append(f"{int(ctx):,} token max context observed")
    rows.append(
        _row("large_context", large_score, large_reasons, large_warnings, metrics)
    )

    multi_reasons = []
    multi_warnings = list(shared_warnings)
    multi_score = 20 * _norm(tps, 30) + 15 * _norm(ctx, 32768)
    if profile == "multi_gpu":
        multi_score += 35
        multi_reasons.append("benchmark ran with multi-GPU placement")
    if len(gpu_deltas) >= 2:
        multi_score += 20
        multi_reasons.append("VRAM grew on multiple GPUs")
    elif gpu_deltas:
        multi_warnings.append("VRAM delta only shows one GPU")
    else:
        multi_warnings.append("VRAM deltas unavailable; GPU spread is unknown")
    if size_bytes and size_bytes >= 12 * 1024**3:
        multi_score += 10
        multi_reasons.append("large model size may benefit from split placement")
    rows.append(
        _row("multi_gpu", multi_score, multi_reasons, multi_warnings, metrics, needs_gpu=True)
    )

    return sorted(rows, key=lambda row: (row["score"], row["confidence"]), reverse=True)


def _row(
    use_case: str,
    score: float,
    reasons: list[str],
    warnings: list[str],
    metrics: dict[str, Any],
    *,
    needs_gpu: bool = False,
) -> dict[str, Any]:
    clean_reasons = reasons or ["insufficient measured facts for a positive recommendation"]
    clean_warnings = list(dict.fromkeys(warnings))
    score_i = _clamp_score(score)
    confidence = _confidence(metrics, clean_warnings, needs_gpu=needs_gpu)
    if confidence == "insufficient":
        score_i = min(score_i, 25)
    elif confidence == "low":
        score_i = min(score_i, 55)
    return {
        "use_case": use_case,
        "score": score_i,
        "confidence": confidence,
        "reasons": clean_reasons[:4],
        "warnings": clean_warnings[:4],
    }
