"""
Astro Cortex - Time and twilight calculations.

Wraps Skyfield for astronomical time computations. All results are returned
in UTC; callers convert to local time for display if needed.

This module is the single source of truth for:
- Sunset / sunrise times
- Civil / nautical / astronomical twilight boundaries
- Golden windows (periods where target altitude > threshold AND is_dark)
- Moonrise / moonset
- Moon phase and illumination percentage

Why this is separate from app/sources/skyfield_local.py:
- skyfield_local.py is a SOURCE (returns raw ephemeris data)
- This module uses that data to compute DERIVED windows (golden_windows)
- The rating engine and crawl heavy use this for scheduling decisions
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from app.sources.base import Location


# Twilight threshold altitudes (sun altitude below horizon)
TWILIGHT_CIVIL = -6.0       # Bright planets visible
TWILIGHT_NAUTICAL = -12.0   # Enough dark for deep-sky framing
TWILIGHT_ASTRONOMICAL = -18.0  # Full dark, deep-sky imaging


def next_sunset(location: Location, after: datetime) -> datetime:
    """Return next sunset after `after` in UTC."""
    # TODO: implement using Skyfield almanac
    raise NotImplementedError


def next_astronomical_dark(location: Location, after: datetime) -> tuple[datetime, datetime]:
    """Return (start, end) of next astronomical-dark window."""
    raise NotImplementedError


def moon_illumination(when: datetime) -> float:
    """Return moon illumination percentage (0-100) at given time."""
    raise NotImplementedError


def moon_phase(when: datetime) -> str:
    """Return moon phase name: 'new', 'waxing_crescent', 'first_quarter',
    'waxing_gibbous', 'full', 'waning_gibbous', 'last_quarter', 'waning_crescent'."""
    raise NotImplementedError


def compute_golden_windows(
    location: Location,
    target_altitude_min_deg: float,
    start: datetime,
    end: datetime,
    step_minutes: int = 15,
) -> list[tuple[datetime, datetime]]:
    """Find windows where target altitude >= target_altitude_min_deg AND
    sun_altitude < TWILIGHT_NAUTICAL.

    Returns list of (window_start, window_end) tuples in UTC.
    """
    # TODO: implement using Skyfield
    # 1. Iterate from start to end in step_minutes increments
    # 2. Compute target altitude at each step
    # 3. Compute sun altitude at each step
    # 4. Find contiguous runs where both conditions hold
    # 5. Merge adjacent runs (within 1 step gap)
    return []
