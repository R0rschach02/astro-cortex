"""
Astro Cortex - Heavy crawl (30-minute full aggregation + rating).

Runs every 30 minutes via systemd timer. Responsibilities:
- For each active location, fetch from ALL sources via cascade
- Normalize to canonical schema
- Compute rating (DSO mode default, planetary mode if target is planet)
- Write new row to crawls table (append-only)
- Write new row to ratings table (1:1 with crawl)
- Generate forecast_log entries for next 48h (lead time)
- Update PWA cache (latest-wins JSON for the app)

This is the HEAVY tick — it does fetch new data. Each call may launch
Playwright (for ClearOutside) and hit 4-5 API endpoints per location.
Budget: with 4 locations and 30-min cadence, that's 192 calls/hour —
well within all sources' rate limits.

Concurrency:
- Sources within one location are fetched concurrently (asyncio.gather)
- Locations are processed sequentially (avoids Playwright browser contention)
- DB writes are serialized via fcntl file_lock()
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from app.config import settings
from app.db.operations import (
    db_session,
    file_lock,
    insert_crawl,
    insert_forecast_log,
    get_active_locations,
)
from app.engine.rating import NormalizedObservation, ObservingMode, rate
from app.sources.cascade import Cascade
from app.sources.brightsky import BrightSkySource
from app.sources.clearoutside import ClearOutsideSource
from app.sources.dwd import DWDSource
from app.sources.meteoblue import MeteoblueSource
from app.sources.open_meteo import OpenMeteoSource
from app.sources.skyfield_local import SkyfieldSource

log = structlog.get_logger()


def build_cascade() -> Cascade:
    """Instantiate all sources and wire up the cascade.

    Browser sessions (Playwright) are created lazily on first fetch and
    reused for the duration of this process.
    """
    sources = {
        "dwd": DWDSource(),
        "brightsky": BrightSkySource(),
        "clearoutside": ClearOutsideSource(),
        "meteoblue": MeteoblueSource(),
        "open_meteo": OpenMeteoSource(),
        "skyfield": SkyfieldSource(),
    }
    return Cascade(sources)


async def crawl_location(cascade: Cascade, location_id: str) -> None:
    """Fetch + rate one location. Writes crawl + rating + forecasts to DB."""
    now = datetime.now(timezone.utc)

    # TODO: implement properly — this is a stub
    # 1. Get Location object from DB (lat/lon/elevation)
    # 2. cascade.fetch_all(location, now) → (values, provenance)
    # 3. Build NormalizedObservation from values
    # 4. rate(observation, ObservingMode.DSO)
    # 5. Insert crawl row → get crawl_id
    # 6. Insert rating row (1:1 with crawl_id)
    # 7. For each future target_ts in [now+1h, now+48h] step 1h:
    #    - cascade.fetch_forecast(location, target_ts)
    #    - insert_forecast_log(...)
    log.info("crawl_location_stub", location_id=location_id)


async def main_async() -> None:
    """Heavy crawl entry point (async)."""
    log.info("heavy_crawl_start")

    cascade = build_cascade()
    try:
        with db_session() as conn:
            locations = get_active_locations(conn)
        log.info("active_locations", count=len(locations))

        for loc in locations:
            try:
                await crawl_location(cascade, loc["id"])
            except Exception as e:
                log.exception("crawl_location_failed", location_id=loc["id"], error=str(e))

    finally:
        # Close all sources (browser, HTTP clients)
        for source in cascade.sources.values():
            if hasattr(source, "close"):
                await source.close()

    log.info("heavy_crawl_end")


def main() -> None:
    """Heavy crawl entry point. Called by systemd astro-crawler.service."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
