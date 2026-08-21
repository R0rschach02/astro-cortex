"""
Astro Cortex - Source interface.

Every data source (DWD, BrightSky, ClearOutside, Meteoblue, Open-Meteo,
Skyfield-local) implements the Source protocol. This is the contract that
allows the cascade layer to substitute sources transparently.

A source's only job is: fetch raw data for a (location, time) and return
it in its native schema. Normalization happens in app/engine/normalizer.py,
which knows how to map each source's fields to the canonical NormalizedObservation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass
class Location:
    id: str
    name: str
    latitude: float
    longitude: float
    elevation_m: float | None = None


@runtime_checkable
class Source(Protocol):
    """Protocol for all data sources.

    A source is identified by its `name` attribute (used in sources_json
    provenance tracking) and provides one async fetch method.

    Sources must be stateless — same (location, when) → same response
    (modulo the live API returning different data, of course). Caching,
    if any, lives in the source module itself, not in the cascade.
    """

    name: str

    async def fetch(
        self,
        location: Location,
        when: datetime,
    ) -> dict[str, Any] | None:
        """Fetch raw data for a location and time.

        Returns:
            dict with source-native fields, or None on failure.
            The caller (cascade) decides whether to fall back to another
            source or proceed with partial data.

        Raises:
            Should NOT raise — catch all exceptions internally and return None.
            This keeps the cascade simple: None means "try next source".
        """
        ...

    async def fetch_forecast(
        self,
        location: Location,
        target_time: datetime,
    ) -> dict[str, Any] | None:
        """Fetch a forecast for a future time. Default: not supported."""
        return None
