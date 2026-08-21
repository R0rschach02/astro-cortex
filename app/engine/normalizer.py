"""
Astro Cortex - Unit normalizer.

Converts source-native fields into canonical schema. Each source returns raw
fields with source-specific names and units; this module knows the mappings.

Canonical schema (see NormalizedObservation in app/engine/rating.py):
- cloud_cover_pct: percent (0-100)
- wind_kmh: km/h
- wind_gust_kmh: km/h
- seeing_arcsec: arcseconds
- jetstream_ms: m/s at 300 hPa
- dew_point_c: °C
- ambient_c: °C
- humidity_pct: percent
- precipitation_mm: mm/h

Unit conversion helpers are exported for reuse in source modules.
"""

from __future__ import annotations

from typing import Any

from app.engine.rating import NormalizedObservation


# ---------------------------------------------------------------------------
# Unit conversion helpers
# ---------------------------------------------------------------------------

MS_TO_KMH = 3.6
MPH_TO_KMH = 1.609344
K_TO_C = -273.15
F_TO_C = lambda f: (f - 32) * 5.0 / 9.0


def ms_to_kmh(value: float | None) -> float | None:
    return value * MS_TO_KMH if value is not None else None


def mph_to_kmh(value: float | None) -> float | None:
    return value * MPH_TO_KMH if value is not None else None


def kelvin_to_celsius(value: float | None) -> float | None:
    return value + K_TO_C if value is not None else None


def fahrenheit_to_celsius(value: float | None) -> float | None:
    return F_TO_C(value) if value is not None else None


# ---------------------------------------------------------------------------
# Source-specific normalizers
# ---------------------------------------------------------------------------

def from_dwd(raw: dict[str, Any]) -> NormalizedObservation:
    """Normalize DWD MOSMIX fields.

    DWD uses SI: wind in m/s, temperature in Kelvin, cloud in percent.
    """
    return NormalizedObservation(
        cloud_cover_pct=raw.get("cloud_cover"),
        wind_kmh=ms_to_kmh(raw.get("wind_speed")),
        wind_gust_kmh=ms_to_kmh(raw.get("wind_gust")),
        dew_point_c=kelvin_to_celsius(raw.get("dew_point")),
        ambient_c=kelvin_to_celsius(raw.get("temperature")),
        humidity_pct=raw.get("relative_humidity"),
        precipitation_mm=raw.get("precipitation"),
        seeing_arcsec=None,       # DWD doesn't provide seeing
        jetstream_ms=None,        # DWD doesn't provide jetstream
    )


def from_brightsky(raw: dict[str, Any]) -> NormalizedObservation:
    """Normalize BrightSky fields. BrightSky is mostly already in canonical units."""
    return NormalizedObservation(
        cloud_cover_pct=raw.get("cloud_cover"),
        wind_kmh=raw.get("wind_speed"),
        wind_gust_kmh=raw.get("wind_gust"),
        dew_point_c=raw.get("dew_point"),
        ambient_c=raw.get("temperature"),
        humidity_pct=raw.get("relative_humidity"),
        precipitation_mm=raw.get("precipitation"),
        seeing_arcsec=None,
        jetstream_ms=None,
    )


def from_meteoblue(raw: dict[str, Any]) -> NormalizedObservation:
    """Normalize Meteoblue fields. Meteoblue is the only source with seeing/jetstream."""
    return NormalizedObservation(
        seeing_arcsec=raw.get("seeing"),
        jetstream_ms=raw.get("wind_300hPa"),
        cloud_cover_pct=raw.get("cloud_cover"),
        wind_kmh=ms_to_kmh(raw.get("wind_speed")),
        dew_point_c=raw.get("dew_point"),
        ambient_c=raw.get("temperature"),
    )


def from_open_meteo(raw: dict[str, Any]) -> NormalizedObservation:
    """Normalize Open-Meteo fields. Open-Meteo uses SI for wind."""
    return NormalizedObservation(
        cloud_cover_pct=raw.get("cloud_cover"),
        wind_kmh=ms_to_kmh(raw.get("wind_speed_10m")),
        wind_gust_kmh=ms_to_kmh(raw.get("wind_gusts_10m")),
        dew_point_c=raw.get("dew_point_2m"),
        ambient_c=raw.get("temperature_2m"),
        humidity_pct=raw.get("relative_humidity_2m"),
        precipitation_mm=raw.get("precipitation"),
        seeing_arcsec=None,
        jetstream_ms=None,
    )


def from_clearoutside(raw: dict[str, Any]) -> NormalizedObservation:
    """Normalize ClearOutside fields. ClearOutside uses mph for wind, °C for temp."""
    return NormalizedObservation(
        cloud_cover_pct=raw.get("cloud_total"),
        wind_kmh=mph_to_kmh(raw.get("wind_surface")),
        dew_point_c=raw.get("dew_point"),
        ambient_c=raw.get("temp"),
        precipitation_mm=raw.get("precip_rate"),
        seeing_arcsec=None,
        jetstream_ms=None,
    )
