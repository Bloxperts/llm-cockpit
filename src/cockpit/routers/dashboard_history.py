"""Dashboard history endpoint (UC-03).

    GET /api/dashboard/history?range=24h|7d&metric=gpu_temp|vram|calls|latency|tokens

Reads the down-sampled `metrics_snapshot_minute` / `metrics_snapshot_hour`
tables (populated by `services.aggregator`) for the GPU metrics, and the
`messages` table for the call-rate / latency / tokens metrics.

All five metrics return a uniform shape — `{ series: [ { label, data } ] }`
— so the frontend can render them with a single chart abstraction. One
or more series per metric:

| metric    | series                                  | source                    |
|-----------|------------------------------------------|---------------------------|
| gpu_temp  | one per GPU index ("GPU 0", "GPU 1", …)  | metrics_snapshot_minute / hour |
| vram      | one per GPU index                        | same                       |
| calls     | "Calls"                                  | messages (assistant rows)  |
| latency   | "p50", "p95"                             | messages (assistant rows)  |
| tokens    | "Input tokens", "Output tokens"          | messages (assistant rows)  |

Auth: `current_user_must_be_settled` — same gate as the live dashboard.
A user who hasn't completed UC-09 forced password change can't read
history either.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from cockpit.deps import get_session
from cockpit.models import User
from cockpit.routers.auth import current_user_must_be_settled

log = logging.getLogger(__name__)
router = APIRouter()

RangeT = Literal["24h", "7d"]
MetricT = Literal["gpu_temp", "vram", "calls", "latency", "tokens"]


# --- Response schema ------------------------------------------------------


class HistoryPoint(BaseModel):
    ts: str  # ISO 8601, naive UTC ("YYYY-MM-DDTHH:MM:SS")
    value: float | None


class HistorySeries(BaseModel):
    label: str
    data: list[HistoryPoint]


class HistoryResponse(BaseModel):
    series: list[HistorySeries]


# --- Helpers --------------------------------------------------------------


def _now_naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _range_window(range_: RangeT) -> tuple[datetime, str]:
    """Return (start_ts, sql_strftime_pattern) for the given range.

    The strftime pattern is used by the messages-table queries to bucket
    rows into minute or hour granularity.
    """
    now = _now_naive_utc()
    if range_ == "24h":
        return now - timedelta(hours=24), "%Y-%m-%dT%H:%M:00"
    return now - timedelta(days=7), "%Y-%m-%dT%H:00:00"


def _gpu_table(range_: RangeT) -> str:
    return "metrics_snapshot_minute" if range_ == "24h" else "metrics_snapshot_hour"


def _percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolation percentile, equivalent to numpy's default. Used
    for the latency p50 / p95 series. SQLite has no PERCENTILE_DISC."""
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (n - 1)
    lo = int(rank)
    frac = rank - lo
    if lo + 1 >= n:
        return sorted_vals[-1]
    return sorted_vals[lo] + frac * (sorted_vals[lo + 1] - sorted_vals[lo])


def _iso(dt: datetime | str) -> str:
    if isinstance(dt, str):
        # SQLite returns the bucket_ts as a string when read via raw text(...).
        # Strip any trailing fractional seconds for a stable ISO shape.
        return dt.split(".")[0].replace(" ", "T")
    return dt.isoformat(sep="T", timespec="seconds")


# --- Per-metric query builders -------------------------------------------


def _gpu_metric(
    db: Session, range_: RangeT, column: str, label_prefix: str = "GPU"
) -> list[HistorySeries]:
    """Used for both `gpu_temp` (column = temp_c_avg) and `vram`
    (column = vram_used_mb_avg). Returns one series per gpu_index."""
    table = _gpu_table(range_)
    start, _ = _range_window(range_)
    rows = db.execute(
        text(
            f"""
            SELECT gpu_index, bucket_ts, {column} AS value
            FROM {table}
            WHERE bucket_ts >= :start
            ORDER BY gpu_index, bucket_ts
            """
        ),
        {"start": start},
    ).fetchall()

    by_gpu: dict[int, list[HistoryPoint]] = {}
    for gpu_index, bucket_ts, value in rows:
        by_gpu.setdefault(gpu_index, []).append(
            HistoryPoint(
                ts=_iso(bucket_ts),
                value=float(value) if value is not None else None,
            )
        )
    return [
        HistorySeries(label=f"{label_prefix} {gpu_index}", data=points)
        for gpu_index, points in sorted(by_gpu.items())
    ]


def _calls_metric(db: Session, range_: RangeT) -> list[HistorySeries]:
    start, pattern = _range_window(range_)
    rows = db.execute(
        text(
            """
            SELECT strftime(:pattern, ts) AS bucket, COUNT(*) AS value
            FROM messages
            WHERE ts >= :start
              AND role = 'assistant'
            GROUP BY strftime(:pattern, ts)
            ORDER BY bucket
            """
        ),
        {"start": start, "pattern": pattern},
    ).fetchall()
    return [
        HistorySeries(
            label="Calls",
            data=[HistoryPoint(ts=_iso(b), value=float(v)) for b, v in rows],
        )
    ]


def _latency_metric(db: Session, range_: RangeT) -> list[HistorySeries]:
    """Two series: p50 and p95. Pulled per-bucket and computed in Python."""
    start, pattern = _range_window(range_)
    rows = db.execute(
        text(
            """
            SELECT strftime(:pattern, ts) AS bucket, latency_ms
            FROM messages
            WHERE ts >= :start
              AND role = 'assistant'
              AND latency_ms IS NOT NULL
            ORDER BY bucket
            """
        ),
        {"start": start, "pattern": pattern},
    ).fetchall()

    bucketed: dict[str, list[float]] = {}
    for bucket, latency_ms in rows:
        bucketed.setdefault(bucket, []).append(float(latency_ms))

    p50: list[HistoryPoint] = []
    p95: list[HistoryPoint] = []
    for bucket in sorted(bucketed):
        vals = bucketed[bucket]
        p50.append(HistoryPoint(ts=_iso(bucket), value=_percentile(vals, 50.0)))
        p95.append(HistoryPoint(ts=_iso(bucket), value=_percentile(vals, 95.0)))
    return [
        HistorySeries(label="p50", data=p50),
        HistorySeries(label="p95", data=p95),
    ]


def _tokens_metric(db: Session, range_: RangeT) -> list[HistorySeries]:
    start, pattern = _range_window(range_)
    rows = db.execute(
        text(
            """
            SELECT strftime(:pattern, ts) AS bucket,
                   COALESCE(SUM(usage_in), 0)  AS in_tokens,
                   COALESCE(SUM(usage_out), 0) AS out_tokens
            FROM messages
            WHERE ts >= :start
              AND role = 'assistant'
            GROUP BY strftime(:pattern, ts)
            ORDER BY bucket
            """
        ),
        {"start": start, "pattern": pattern},
    ).fetchall()

    in_series = [
        HistoryPoint(ts=_iso(b), value=float(in_t)) for b, in_t, _ in rows
    ]
    out_series = [
        HistoryPoint(ts=_iso(b), value=float(out_t)) for b, _, out_t in rows
    ]
    return [
        HistorySeries(label="Input tokens", data=in_series),
        HistorySeries(label="Output tokens", data=out_series),
    ]


# --- Endpoint -------------------------------------------------------------


@router.get(
    "/history",
    response_model=HistoryResponse,
    summary="Dashboard history time series (UC-03).",
)
def get_history(
    request: Request,
    range: RangeT = Query(...),
    metric: MetricT = Query(...),
    user: User = Depends(current_user_must_be_settled),
    db: Session = Depends(get_session),
) -> HistoryResponse:
    if metric == "gpu_temp":
        series = _gpu_metric(db, range, "temp_c_avg")
    elif metric == "vram":
        series = _gpu_metric(db, range, "vram_used_mb_avg")
    elif metric == "calls":
        series = _calls_metric(db, range)
    elif metric == "latency":
        series = _latency_metric(db, range)
    else:  # metric == "tokens"
        series = _tokens_metric(db, range)
    return HistoryResponse(series=series)
