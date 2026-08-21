"""
Astro Cortex - Daily milestone check.

Runs once per day (anchored to settings.milestone_check_hour) within the
radar cycle. Uses a date-stamped marker in state.json to ensure it runs
exactly once per day even if the radar tick is restarted.

Responsibilities:
- Run forecast_verifier (auto-verify past forecasts against actuals)
- Compute and log rolling error statistics (future, once enough data)
- Any other daily maintenance (DB vacuum, log rotation, etc.)

Why not a separate systemd timer?
- Avoids another unit to manage
- Reuses the existing radar tick infrastructure
- One-shot daily logic doesn't justify a separate timer
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import structlog

from app.config import settings
from app.engine.verifier import verify_pending_forecasts

log = structlog.get_logger()


STATE_FILE = settings.state_dir / "milestone.json"


def should_run_today() -> bool:
    """Return True if milestone has not yet run today."""
    if not STATE_FILE.exists():
        return True
    try:
        state = json.loads(STATE_FILE.read_text())
        last_run = state.get("last_run_date")
        return last_run != date.today().isoformat()
    except (json.JSONDecodeError, KeyError):
        return True


def mark_run_today() -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({
        "last_run_date": date.today().isoformat(),
        "last_run_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))


def run() -> None:
    """Run the daily milestone if not yet run today."""
    if not should_run_today():
        log.info("milestone_already_run_today")
        return

    log.info("milestone_run_start")

    # 1. Verify pending forecasts (auto-attribution against actuals)
    try:
        result = verify_pending_forecasts()
        log.info("milestone_forecast_verification", **result)
    except Exception:
        log.exception("milestone_verification_failed")

    # 2. TODO: Compute rolling error statistics (once enough data exists)
    # 3. TODO: DB maintenance (VACUUM ANALYZE if row count is high)

    mark_run_today()
    log.info("milestone_run_end")


if __name__ == "__main__":
    run()
