"""
Astro Cortex - ClearOutside source.

ClearOutside (https://clearoutside.com/) provides astronomy-tailored
forecasts: cloud cover at low/mid/high altitudes, dew point, transparency,
seeing estimate. Accessed via Playwright because the site is JavaScript-rendered.

Strengths:
- Astronomy-specific cloud/dew forecast (next 6h good, 12h usable)
- Transparency score (useful for DSO)
- Native cloud cover percentages

Limitations:
- Cloudflare-protected → must use Playwright with stealth (high per-call cost)
- No multi-day forecast (beyond ~12h degrades sharply)
- No raw API — must parse DOM

Field mapping (ClearOutside → canonical):
    cloud_total → cloud_cover_pct
    cloud_low / cloud_mid / cloud_high → kept in raw_json for analysis
    wind_surface → wind_kmh (mph → km/h: × 1.609)
    dew_point → dew_point_c
    temp → ambient_c
    precip_rate → precipitation_mm

Budget consideration: ClearOutside calls are expensive (Playwright browser
launch + Cloudflare challenge). Use only for the primary location per
heavy-crawl cycle, not for all locations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from playwright.async_api import async_playwright

from app.config import settings
from app.sources.base import Location, Source

log = structlog.get_logger()


class ClearOutsideSource:
    """ClearOutside web-scraped source. Implements Source protocol."""

    name = "clearoutside"
    BASE_URL = "https://clearoutside.com/forecast/"

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None

    async def _ensure_browser(self) -> None:
        """Lazily launch Playwright Chromium (stealth mode).

        Browser is reused across calls within one process; closed on
        shutdown via close(). Do NOT launch per-call.
        """
        if self._browser is not None:
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )

    async def fetch(self, location: Location, when: datetime) -> dict[str, Any] | None:
        """Scrape current forecast for location.

        TODO: implement. Steps:
        1. Ensure browser is running
        2. Open new context with stealth user-agent
        3. Navigate to BASE_URL/{lat}/{lon}
        4. Wait for forecast table to render
        5. Extract current-hour row, map to canonical fields
        6. Close context (keep browser alive)
        """
        log.warning("clearoutside_fetch_not_implemented", location=location.id)
        return None

    async def fetch_forecast(
        self, location: Location, target_time: datetime
    ) -> dict[str, Any] | None:
        """ClearOutside supports ~12h horizon. Beyond that, return None.

        The cascade will fall back to Open-Meteo for longer lead times.
        """
        log.warning("clearoutside_forecast_not_implemented", location=location.id)
        return None

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
