"""
Astro Cortex - Fallback cascade.

For each parameter (cloud, wind, seeing, dew, jetstream), sources are tried
in priority order until one returns a usable value. This is the resilience
layer: if Meteoblue is down, Open-Meteo covers seeing; if DWD fails,
BrightSky covers cloud.

Priority is defined per-parameter, not per-source, because sources have
different strengths:
- DWD/BrightSky: best for cloud, wind, precipitation (ground truth + model)
- Open-Meteo: best for multi-day forecasts (open, generous API limits)
- ClearOutside: astronomy-specific cloud/dew forecast (good for next 6h)
- Meteoblue: best for seeing and jetstream (astronomy-specific models)
- Skyfield (local): ephemeris (sun, moon, twilight) — never fails, no network

Configuration: edit the PRIORITY dict below. To add a new source, implement
app.sources.base.Source and add its name to the appropriate parameter lists.
"""

from __future__ import annotations

import structlog
from datetime import datetime
from typing import Any

from app.sources.base import Location, Source

log = structlog.get_logger()


# Priority order per parameter. First entry = preferred source.
# If a source returns None for this parameter, the cascade moves on.
PRIORITY: dict[str, list[str]] = {
    "cloud_cover_pct":    ["dwd", "brightsky", "open_meteo", "clearoutside"],
    "wind_kmh":           ["dwd", "brightsky", "open_meteo"],
    "wind_gust_kmh":      ["dwd", "brightsky", "open_meteo"],
    "seeing_arcsec":      ["meteoblue", "open_meteo"],
    "jetstream_ms":       ["meteoblue", "open_meteo"],
    "dew_point_c":        ["dwd", "brightsky", "open_meteo"],
    "ambient_c":          ["dwd", "brightsky", "open_meteo"],
    "humidity_pct":       ["dwd", "brightsky", "open_meteo"],
    "precipitation_mm":   ["dwd", "brightsky", "open_meteo"],
}


class Cascade:
    """Orchestrates parameter-by-parameter fallback across sources.

    Holds references to instantiated Source objects; does not instantiate
    sources per-call (Playwright browser sessions are expensive to spin up).
    """

    def __init__(self, sources: dict[str, Source]):
        self.sources = sources

    async def fetch_all(
        self,
        location: Location,
        when: datetime,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Fetch all parameters for a location, with per-parameter fallback.

        Returns:
            (values, provenance) where values is a dict of canonical parameter
            names to values (or None if no source provided it), and provenance
            maps each parameter name to the source that provided it.
        """
        # Fetch each source once (don't re-fetch per parameter)
        source_payloads: dict[str, dict[str, Any] | None] = {}
        for name, source in self.sources.items():
            try:
                source_payloads[name] = await source.fetch(location, when)
            except Exception as e:
                log.warning("source_fetch_failed", source=name, error=str(e))
                source_payloads[name] = None

        # Resolve each parameter
        values: dict[str, Any] = {}
        provenance: dict[str, str] = {}

        for param, source_priority in PRIORITY.items():
            resolved_value = None
            for source_name in source_priority:
                payload = source_payloads.get(source_name)
                if payload is None:
                    continue
                # Each source module's fetch() returns raw fields; the
                # normalizer knows the field mapping. For cascade purposes,
                # we just check that the source reported a non-None value
                # for this canonical parameter.
                if param in payload and payload[param] is not None:
                    resolved_value = payload[param]
                    provenance[param] = source_name
                    break
            values[param] = resolved_value

        return values, provenance

    async def fetch_forecast(
        self,
        location: Location,
        target_time: datetime,
        lead_hours: float,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Same as fetch_all but for a future target_time.

        Sources that don't support forecasting (ClearOutside, BrightSky for
        long ranges) will return None and the cascade will skip them.
        """
        source_payloads: dict[str, dict[str, Any] | None] = {}
        for name, source in self.sources.items():
            try:
                source_payloads[name] = await source.fetch_forecast(location, target_time)
            except Exception as e:
                log.warning("source_forecast_failed", source=name, error=str(e))
                source_payloads[name] = None

        values: dict[str, Any] = {}
        provenance: dict[str, str] = {}

        for param, source_priority in PRIORITY.items():
            resolved_value = None
            for source_name in source_priority:
                payload = source_payloads.get(source_name)
                if payload is None:
                    continue
                if param in payload and payload[param] is not None:
                    resolved_value = payload[param]
                    provenance[param] = source_name
                    break
            values[param] = resolved_value

        return values, provenance
