"""
Astro Cortex - Skyfield local ephemeris source.

Skyfield (https://rhodesmill.org/skyfield/) computes sun/moon positions
and twilight times locally from a JPL ephemeris file (de421.bsp, 17MB).
This source NEVER fails (no network call) and NEVER falls back — it's the
ground truth for astronomical time computations.

Provides:
- Sunset / sunrise / twilight boundaries (civil, nautical, astronomical)
- Moonrise / moonset / moon phase / moon illumination
- Sun altitude (for "is it dark yet?")
- Target altitude (for DSO/planet observability window)

This source does NOT participate in the cascade for weather parameters.
It is called separately by the rating engine to determine golden windows
(periods where target altitude > min_altitude AND conditions are GO).

Setup:
- de421.bsp is downloaded automatically by Skyfield on first use
- File lives at ~/.skyfield/ or $SKYFIELD_DATA_DIR (see .env.example)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from skyfield.api import load, wgs84

from app.config import settings
from app.sources.base import Location, Source

log = structlog.get_logger()


class SkyfieldSource:
    """Local ephemeris source. Implements Source protocol (weather fields all None)."""

    name = "skyfield"

    def __init__(self) -> None:
        # Use custom loader pointing at configured data dir
        self.loader = load.Loader(str(settings.skyfield_data_dir))
        # de421 is the standard short-period ephemeris (covers 1849-2050)
        # For longer historical analysis, switch to de440 (1500-2650, ~100MB)
        self.eph = self.loader("de421.bsp")
        self.ts = self.loader.timescale()

    async def fetch(self, location: Location, when: datetime) -> dict[str, Any] | None:
        """Return astronomical context (not weather).

        Returns a dict with fields:
        - sun_altitude (degrees)
        - moon_altitude (degrees)
        - moon_illumination (percent)
        - moon_phase (string: 'new', 'waxing_crescent', ..., 'full')
        - is_dark (bool: sun below -12° = no civil twilight)
        - is_astronomical_dark (bool: sun below -18°)
        - twilight_phase (string: 'day', 'civil', 'nautical', 'astronomical', 'night')
        """
        # TODO: implement using Skyfield
        log.warning("skyfield_fetch_not_implemented", location=location.id)
        return None

    async def fetch_forecast(
        self, location: Location, target_time: datetime
    ) -> dict[str, Any] | None:
        """Skyfield is deterministic — forecast == current for ephemeris.

        Returns same shape as fetch(), evaluated at target_time.
        """
        return await self.fetch(location, target_time)

    def compute_twilight_windows(
        self,
        location: Location,
        date: datetime,
        min_sun_altitude_deg: float = -18.0,
    ) -> list[tuple[datetime, datetime]]:
        """Compute dark-sky windows for a date.

        Returns a list of (start, end) tuples in UTC where sun_altitude <
        min_sun_altitude_deg. Typically 1 window per 24h (the night).
        """
        # TODO: implement using Skyfield's almanac module
        return []

    def compute_moon_windows(
        self,
        location: Location,
        date: datetime,
        max_moon_altitude_deg: float = -5.0,
    ) -> list[tuple[datetime, datetime]]:
        """Compute moon-free windows for a date (moon below horizon or low)."""
        # TODO: implement
        return []
