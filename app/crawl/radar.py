"""
Astro Cortex - Radar tick (5-minute lightweight pulse).

Runs every 5 minutes via systemd timer. Responsibilities:
- Check current ratings for all active locations
- Detect state transitions (No-Go → Go, Go → No-Go, wind escalation)
- Send Telegram alerts on transitions (respecting cooldown)
- Update state.json with current session state

This is a LIGHTWEIGHT tick — it does NOT fetch new data. It only reads the
latest crawl row from the DB and the current state.json. All heavy data
fetching happens in heavy.py (30-min timer).

Why split radar and crawler?
- 5-min radar gives responsive alerts (wind danger detected within 5 min)
- 30-min crawler avoids hammering sources (ClearOutside Cloudflare budget!)
- Radar can run even if crawler is failing (uses last-known data)
"""

from __future__ import annotations

import structlog

from app.config import settings

log = structlog.get_logger()


def main() -> None:
    """Radar tick entry point. Called by systemd astro-radar.service."""
    log.info("radar_tick_start")

    # TODO: implement
    # 1. Load state.json
    # 2. For each active location:
    #    a. Read latest crawl row from DB
    #    b. Compute current rating (rating engine, no fetch)
    #    c. Compare to previous state
    #    d. If transition detected: send Telegram alert (with cooldown check)
    # 3. Check wind escalation (warning at 45 km/h, danger at 60 km/h)
    # 4. Persist new state to state.json

    log.info("radar_tick_end")


if __name__ == "__main__":
    main()
