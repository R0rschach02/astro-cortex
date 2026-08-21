"""
Astro Cortex - Forecast Verifier.

Runs once daily (via milestone job in radar cycle) to automatically compare
past forecasts against actual observations. No human intervention required.

Algorithm:
1. Find all forecast_log rows where:
   - target_ts is at least 45 minutes in the past (ensures actual crawl ran)
   - verification_status is NULL
   - target_ts is not older than grace period (24h default)
2. For each, find the nearest crawl row for the same location within
   ±tolerance_minutes (default 20 min).
3. If found: compute errors (predicted - actual) per parameter, insert
   into forecast_verification, mark forecast_log as 'verified'.
4. If no crawl found within tolerance AND target_ts is older than grace
   period: mark forecast_log as 'unverifiable' (final state, no retry).
5. Otherwise: leave pending (will retry next day).

This module is the data foundation for future ML calibration:
- After weeks/months, forecast_verification accumulates enough rows to
  compute error distributions per (source, parameter, lead_time).
- A future calibration step can then bias-correct forecasts based on
  historical error curves.
"""

from __future__ import annotations

import structlog
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.db.operations import (
    db_session,
    file_lock,
    find_nearest_crawl,
    insert_verification,
    pending_verifications,
)

log = structlog.get_logger()


def verify_pending_forecasts() -> dict[str, int]:
    """Run one verification pass. Returns counts: {verified, unverifiable, skipped}.

    Idempotent: safe to run multiple times per day (only touches pending rows).
    """
    cutoff_lead = 45  # minutes
    grace = settings.forecast_grace_period_hours
    tolerance = settings.forecast_tolerance_minutes

    with db_session() as conn, file_lock():
        pending = pending_verifications(
            conn,
            cutoff_lead_minutes=cutoff_lead,
            max_age_hours=grace,
        )

        verified_count = 0
        unverifiable_count = 0
        skipped_count = 0

        for row in pending:
            target_ts = datetime.fromisoformat(row["target_ts"])
            if target_ts.tzinfo is None:
                target_ts = target_ts.replace(tzinfo=timezone.utc)

            age = datetime.now(timezone.utc) - target_ts

            nearest = find_nearest_crawl(conn, row["location_id"], target_ts, tolerance)

            if nearest is not None:
                actuals = {
                    "cloud_cover_pct": nearest["cloud_cover_pct"],
                    "wind_kmh": nearest["wind_kmh"],
                    "seeing_arcsec": nearest["seeing_arcsec"],
                    "dew_point_c": nearest["dew_point_c"],
                }
                errors = {
                    "cloud_cover_pct": _diff(row["cloud_cover_pct"], actuals["cloud_cover_pct"]),
                    "wind_kmh": _diff(row["wind_kmh"], actuals["wind_kmh"]),
                    "seeing_arcsec": _diff(row["seeing_arcsec"], actuals["seeing_arcsec"]),
                    "dew_point_c": _diff(row["dew_point_c"], actuals["dew_point_c"]),
                }
                insert_verification(conn, row["id"], nearest["id"], actuals, errors, tolerance)
                verified_count += 1
                log.info(
                    "forecast_verified",
                    forecast_log_id=row["id"],
                    location_id=row["location_id"],
                    lead_hours=row["lead_time_hours"],
                    error_cloud=errors["cloud_cover_pct"],
                    error_seeing=errors["seeing_arcsec"],
                )
            elif age > timedelta(hours=grace):
                # Grace period exceeded: no crawl ever matched. Mark final.
                insert_verification(conn, row["id"], None, {}, {}, tolerance)
                unverifiable_count += 1
                log.warning(
                    "forecast_unverifiable",
                    forecast_log_id=row["id"],
                    location_id=row["location_id"],
                    age_hours=age.total_seconds() / 3600,
                )
            else:
                # Within grace period, no crawl yet. Leave pending for next run.
                skipped_count += 1

        return {
            "verified": verified_count,
            "unverifiable": unverifiable_count,
            "skipped": skipped_count,
        }


def _diff(predicted: float | None, actual: float | None) -> float | None:
    """Predicted minus actual. Returns None if either side is missing."""
    if predicted is None or actual is None:
        return None
    return predicted - actual
