"""
Astro Cortex - DWD (Deutscher Wetterdienst) source.

DWD provides:
- Current observations via BrightSky API (https://api.brightsky.dev/)
- Forecast via MOSMIX (OpenData)
- Ground truth for verification

This module is a STUB. Implementation TODOs:
- Implement fetch() to query DWD MOSMIX nearest-station forecast
- Implement fetch_forecast() for lead times up to 78h
- Map DWD field names to canonical schema:
    * cloud_cover → cloud_cover_pct
    * wind_speed (m/s) → wind_kmh (× 3.6)
    * wind_gust (m/s) → wind_gust_kmh (× 3.6)
    * dew_point (K) → dew_point_c (− 273.15)
    * temperature (K) → ambient_c (− 273.15)
    * precipitation (mm) → precipitation_mm
- Respect rate limits (DWD OpenData is generous, but be polite)
- Cache Playwright sessions if DWD pages are scraped (not needed for API)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import structlog

from app.config import settings
from app.sources.base import Location, Source

log = structlog.get_logger()


class DWDSource:
    """DWD MOSMIX forecast source. Implements Source protocol."""

    name = "dwd"

    BASE_URL = "https://opendata.dwd.de/weather/weather_reports/synoptic"

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": settings.user_agent},
        )

    async def fetch(self, location: Location, when: datetime) -> dict[str, Any] | None:
        """Fetch current observations from DWD.

        TODO: implement. Use the MOSMIX OpenData endpoint, find the nearest
        station, return a dict with canonical field names already converted
        to canonical units (see module docstring for mapping).
        """
        # TODO: find nearest station by lat/lon
        # TODO: parse MOSMIX XML/SVG for that station
        # TODO: map fields, return dict
        log.warning("dwd_fetch_not_implemented", location=location.id)
        return None

    async def fetch_forecast(
        self, location: Location, target_time: datetime
    ) -> dict[str, Any] | None:
        """Fetch DWD MOSMIX forecast for target_time.

        MOSMIX supports up to 78h lead time with high quality, then degrades.
        """
        # TODO: implement
        log.warning("dwd_forecast_not_implemented", location=location.id)
        return None

    async def close(self) -> None:
        await self.client.aclose()
