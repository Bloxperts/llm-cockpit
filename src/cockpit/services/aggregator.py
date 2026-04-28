"""Down-sample aggregators for the dashboard history view (UC-03).

Two periodic background tasks, modelled on the Sprint 3 `GpuSampler`:

- `MinuteAggregator` runs every 60 s. For each closed wall-minute it
  rolls the matching `metrics_snapshot` rows up into one
  `metrics_snapshot_minute` row per (bucket, gpu_index) — `AVG`/`MAX`
  per the schema. Idempotent on re-run thanks to the
  `UNIQUE(bucket_ts, gpu_index)` constraint and `INSERT OR IGNORE`.
  Also prunes raw `metrics_snapshot` rows older than 7 d so the source
  table stays small.

- `HourAggregator` runs every 3600 s. Same shape, rolling the
  `metrics_snapshot_minute` rows up into `metrics_snapshot_hour`.
  Prunes `metrics_snapshot_minute` rows older than 30 d.

Note on cadence — the UC-03 functional spec says "down-sampling job runs
every hour" / "daily job for hour buckets". We deliberately run the
minute aggregator every 60 s (not hourly) so the 24 h chart updates
within a minute of the most recent sample; an hourly batch would leave
the most recent hour empty. Output granularity (1-min and 1-h buckets)
matches the spec; only the *job* interval differs. Spec to be synced at
sprint review.

Both classes expose `aggregate_once()` for tests; `run()` is the
periodic loop wired into the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)

MINUTE_INTERVAL_S = 60.0
HOUR_INTERVAL_S = 3600.0

# Retention windows. Tunable; documented at the top of the file.
RAW_RETENTION_DAYS = 7
MINUTE_RETENTION_DAYS = 30


def _floor_to_minute(dt: datetime) -> datetime:
    """Truncate to the start of the wall-minute. Tz-naive in / tz-naive out
    (we use naive UTC throughout this module to match SQLite's storage
    semantics — `func.current_timestamp()` returns naive UTC strings)."""
    return dt.replace(second=0, microsecond=0)


def _floor_to_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _now_naive_utc() -> datetime:
    """The DB stores tz-naive UTC; comparisons need to match."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- MinuteAggregator -----------------------------------------------------


class MinuteAggregator:
    """Rolls 5 s `metrics_snapshot` rows into 1-min `metrics_snapshot_minute`
    buckets and prunes raw rows older than 7 d.

    Tests construct one with a `clock` callable (default
    `_now_naive_utc`) so they can pin the "now" wall-clock and assert
    deterministic bucket boundaries.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        interval_s: float = MINUTE_INTERVAL_S,
        clock: Callable[[], datetime] = _now_naive_utc,
        run_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session_factory = session_factory
        self.interval_s = interval_s
        self._clock = clock
        self._run_clock = run_clock

    def aggregate_once(self) -> None:
        """Aggregate the most recent *closed* minute into
        `metrics_snapshot_minute`, then prune old raw rows.

        Closed-minute semantics: if `now = 12:34:17`, the closed minute is
        `12:33:00 .. 12:34:00`. The current minute (`12:34:00 .. 12:35:00`)
        is left for the next tick once it closes — this means the
        in-progress minute is never partially aggregated.
        """
        try:
            self._aggregate(self.session_factory, self._clock())
        except Exception as exc:
            log.warning("MinuteAggregator.aggregate_once: %s", exc)

    @staticmethod
    def _aggregate(
        session_factory: sessionmaker[Session], now: datetime
    ) -> None:
        bucket_end = _floor_to_minute(now)
        bucket_start = bucket_end - timedelta(minutes=1)
        retention_cutoff = now - timedelta(days=RAW_RETENTION_DAYS)

        with session_factory() as session:
            # Idempotent upsert via INSERT OR IGNORE keyed on the unique
            # (bucket_ts, gpu_index) constraint. No need for an UPDATE
            # branch — a closed bucket never gains new rows.
            session.execute(
                text(
                    """
                    INSERT OR IGNORE INTO metrics_snapshot_minute
                        (bucket_ts, gpu_index, vram_used_mb_avg,
                         temp_c_avg, temp_c_max, power_w_avg, sample_count)
                    SELECT
                        :bucket_start AS bucket_ts,
                        gpu_index,
                        AVG(vram_used_mb) AS vram_used_mb_avg,
                        AVG(temp_c) AS temp_c_avg,
                        MAX(temp_c) AS temp_c_max,
                        AVG(power_w) AS power_w_avg,
                        COUNT(*) AS sample_count
                    FROM metrics_snapshot
                    WHERE ts >= :bucket_start AND ts < :bucket_end
                    GROUP BY gpu_index
                    """
                ),
                {"bucket_start": bucket_start, "bucket_end": bucket_end},
            )
            # Prune raw rows older than the retention window.
            session.execute(
                text("DELETE FROM metrics_snapshot WHERE ts < :cutoff"),
                {"cutoff": retention_cutoff},
            )
            session.commit()

    async def run(self) -> None:
        while True:
            try:
                # The DB call is sync; run on the event-loop thread is fine
                # for SQLite + small windows (a few hundred rows max).
                self.aggregate_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("MinuteAggregator.run: %s", exc)
            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                raise


# --- HourAggregator ------------------------------------------------------


class HourAggregator:
    """Rolls 1-min `metrics_snapshot_minute` rows into 1-h
    `metrics_snapshot_hour` buckets and prunes minute rows older than 30 d.
    """

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        interval_s: float = HOUR_INTERVAL_S,
        clock: Callable[[], datetime] = _now_naive_utc,
        run_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session_factory = session_factory
        self.interval_s = interval_s
        self._clock = clock
        self._run_clock = run_clock

    def aggregate_once(self) -> None:
        try:
            self._aggregate(self.session_factory, self._clock())
        except Exception as exc:
            log.warning("HourAggregator.aggregate_once: %s", exc)

    @staticmethod
    def _aggregate(
        session_factory: sessionmaker[Session], now: datetime
    ) -> None:
        bucket_end = _floor_to_hour(now)
        bucket_start = bucket_end - timedelta(hours=1)
        retention_cutoff = now - timedelta(days=MINUTE_RETENTION_DAYS)

        with session_factory() as session:
            session.execute(
                text(
                    """
                    INSERT OR IGNORE INTO metrics_snapshot_hour
                        (bucket_ts, gpu_index, vram_used_mb_avg,
                         temp_c_avg, temp_c_max, power_w_avg, sample_count)
                    SELECT
                        :bucket_start AS bucket_ts,
                        gpu_index,
                        AVG(vram_used_mb_avg) AS vram_used_mb_avg,
                        AVG(temp_c_avg) AS temp_c_avg,
                        MAX(temp_c_max) AS temp_c_max,
                        AVG(power_w_avg) AS power_w_avg,
                        SUM(sample_count) AS sample_count
                    FROM metrics_snapshot_minute
                    WHERE bucket_ts >= :bucket_start AND bucket_ts < :bucket_end
                    GROUP BY gpu_index
                    """
                ),
                {"bucket_start": bucket_start, "bucket_end": bucket_end},
            )
            session.execute(
                text(
                    "DELETE FROM metrics_snapshot_minute WHERE bucket_ts < :cutoff"
                ),
                {"cutoff": retention_cutoff},
            )
            session.commit()

    async def run(self) -> None:
        while True:
            try:
                self.aggregate_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("HourAggregator.run: %s", exc)
            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                raise
