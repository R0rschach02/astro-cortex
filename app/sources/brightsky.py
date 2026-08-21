"""
Astro Cortex - BrightSky source.

BrightSky (https://api.brightsky.dev/) is a wrapper around DWD weather
stations that provides JSON API access to current observations and a
limited forecast horizon. Free, no API key required.

Strengths:
- Cloud cover (current observation, ground truth)
- Wind (speed + gust, km/h native)
- Temperature, dew point (°C native)
- Precipitation (mm/h)

Limitations:
- Only German stations (lat/lon must be within Germany)
- No seeing/jetstream (those come from Meteoblue)
- Forecast horizon limited (~24h useful)

Field mapping (BrightSky → canonical):
    cloud_cover → cloud_cover_pct (already percent)
    wind_speed → wind_kmh (already km/h)
    wind_gust → wind_gust_kmh
    dew_point → dew_point_c
    temperature → ambient_c
    relative_humidity → humidity_pct
    precipitation → precipitation_mm
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import structlog

from app.config import settings
from app.sources.base import Location, Source

log = structlog.get_logger()


class BrightSkySource:
    """BrightSky JSON API source. Implements Source protocol."""

    name = "brightsky"
    BASE_URL = "https://api.brightsky.dev"

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": settings.user_agent},
        )

    async def fetch(self, location: Location, when: datetime) -> dict[str, Any] | None:
        """Fetch current weather for a location.

        TODO: implement using GET /current_weather?lat=...&lon=...
        """
        log.warning("brightsky_fetch_not_implemented", location=location.id)
        return None

    async def fetch_forecast(
        self, location: Location, target_time: datetime
    ) -> dict[str, Any] | None:
        """Fetch forecast via GET /weather?lat=...&lon=...&date=...

        BrightSky supports ~24h forecast horizon.
        """
        log.warning("brightsky_forecast_not_implemented", location=location.id)
        return None

    async def close(self) -> None:
        await self.client.aclose()
