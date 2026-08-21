"""
Astro Cortex - Meteoblue source.

Meteoblue provides astronomy-specific models that no other free source offers:
- Seeing (atmospheric turbulence, arcseconds) — NMM7 model
- Jetstream (300 hPa wind speed, m/s)

These two parameters are CRITICAL for DSO and planetary imaging decisions.
No fallback exists — if Meteoblue fails, seeing/jetstream stay None and the
rating engine applies the neutral 0.5 penalty for missing values.

Access: requires API key (free tier available for personal use).
URL: https://www.meteoblue.com/en/weather/api

Field mapping (Meteoblue → canonical):
    seeing → seeing_arcsec (already arcsec)
    wind_300hPa → jetstream_ms (already m/s)
    cloud_cover → cloud_cover_pct
    wind_speed → wind_kmh (m/s → km/h: × 3.6)
    temperature → ambient_c
    dew_point → dew_point_c

Note on horizon: Meteoblue NMM7 provides seeing up to ~3 days, after which
the model degrades sharply. The forecast verifier will eventually quantify
this error curve.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx
import structlog

from app.config import settings
from app.sources.base import Location, Source

log = structlog.get_logger()


class MeteoblueSource:
    """Meteoblue JSON API source. Implements Source protocol."""

    name = "meteoblue"
    BASE_URL = "https://my.meteoblue.com"

    def __init__(self) -> None:
        self.api_key = os.environ.get("METEOBLUE_API_KEY", "")
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": settings.user_agent},
        )

    async def fetch(self, location: Location, when: datetime) -> dict[str, Any] | None:
        """Fetch current forecast from Meteoblue.

        TODO: implement using the MultiAPI endpoint with API key.
        """
        if not self.api_key:
            log.warning("meteoblue_no_api_key")
            return None
        log.warning("meteoblue_fetch_not_implemented", location=location.id)
        return None

    async def fetch_forecast(
        self, location: Location, target_time: datetime
    ) -> dict[str, Any] | None:
        """Meteoblue supports up to ~3 days seeing forecast.

        Beyond that, the cascade should mark seeing_arcsec as None and the
        forecast_log entry should carry `seeing_horizon: true` to indicate
        the parameter was intentionally omitted.
        """
        if not self.api_key:
            return None
        log.warning("meteoblue_forecast_not_implemented", location=location.id)
        return None

    async def close(self) -> None:
        await self.client.aclose()
