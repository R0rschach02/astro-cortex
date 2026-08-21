"""
Astro Cortex - Open-Meteo source.

Open-Meteo (https://open-meteo.com/) provides a free, no-API-key weather
forecast API with global coverage. Used as the fallback for nearly every
parameter when primary sources fail.

Strengths:
- 7-day forecast horizon (degrades after ~3 days, but usable)
- No API key required for non-commercial use
- Generous rate limit (10k calls/day)
- Covers cloud, wind, dew, temperature, precipitation

Limitations:
- No astronomy-specific seeing model (use Meteoblue instead)
- No jetstream at 300 hPa (use Meteoblue instead)

Field mapping (Open-Meteo → canonical):
    cloud_cover → cloud_cover_pct
    wind_speed_10m → wind_kmh (m/s → km/h: × 3.6)
    wind_gusts_10m → wind_gust_kmh
    dew_point_2m → dew_point_c
    temperature_2m → ambient_c
    relative_humidity_2m → humidity_pct
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


class OpenMeteoSource:
    """Open-Meteo JSON API source. Implements Source protocol."""

    name = "open_meteo"
    BASE_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": settings.user_agent},
        )

    async def fetch(self, location: Location, when: datetime) -> dict[str, Any] | None:
        """Fetch current forecast from Open-Meteo.

        Open-Meteo returns hourly forecasts; we pick the hour matching `when`.
        """
        log.warning("open_meteo_fetch_not_implemented", location=location.id)
        return None

    async def fetch_forecast(
        self, location: Location, target_time: datetime
    ) -> dict[str, Any] | None:
        """Open-Meteo supports up to 7-day forecast."""
        log.warning("open_meteo_forecast_not_implemented", location=location.id)
        return None

    async def close(self) -> None:
        await self.client.aclose()
