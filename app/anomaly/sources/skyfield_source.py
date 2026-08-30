"""Skyfield-Source: Himmelskoerper- und ISS-Positionen fuer die Anomalie-
Klassifikation. Kein Stub mehr - echte Berechnung:

- Sonne/Mond/Venus: skyfield + de421.bsp (liegt lokal in ~/.skyfield,
  identische Basis wie der Mond-Teil des Astro-Crawlers; kein Netz).
- ISS: skyfield EarthSatellite mit TLE. TLE wird INJIZIERT (Tests,
  Festwert) oder optional frisch geladen; ohne TLE liefert die Funktion
  None statt Kaputte Werte.

Alle Funktionen sind fuer einen Zeitpunkt + Beobachterstandort rein
berechnend (deterministisch bei gleicher BSP/TLE-Eingabe).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

_SKYFIELD_DIR = os.path.expanduser("~/.skyfield")
DE421 = os.path.join(_SKYFIELD_DIR, "de421.bsp")

_BODIES = {"sun": "sun", "moon": "moon", "venus": "venus",
           "mars": "mars", "jupiter": "jupiter barycenter",
           "saturn": "saturn barycenter"}

_load_cache = {}


def _ts():
    from skyfield.api import load
    return load.timescale(builtin=True)


def _ephem():
    if "bsp" not in _load_cache:
        from skyfield.api import load
        _load_cache["bsp"] = load(DE421)
    return _load_cache["bsp"]


def _observer(lat: float, lon: float, alt_m: float = 0.0):
    from skyfield.api import wgs84
    return wgs84.latlon(lat, lon, elevation_m=alt_m)


def body_altaz(name: str, dt_utc: datetime, lat: float, lon: float,
               alt_m: float = 0.0) -> Optional[dict]:
    """Alt/Az (Grad, ungebrochen) eines Himmelskoerpers am Beobachtungs-
    ort. None bei unbekanntem Koerper oder fehlender Ephemeride."""
    target = _BODIES.get(name)
    if target is None:
        return None
    try:
        t = _ts().from_datetime(dt_utc.replace(tzinfo=timezone.utc)
                                if dt_utc.tzinfo is None else dt_utc)
        obs = _observer(lat, lon, alt_m)
        earth = _ephem()["earth"]
        apparent = (earth + obs).at(t).observe(_ephem()[target]).apparent()
        alt, az, _ = apparent.altaz()
        return {"alt_deg": round(alt.degrees, 4),
                "az_deg": round(az.degrees % 360.0, 4)}
    except Exception:
        return None


def iss_altaz(tle_line1: str, tle_line2: str, dt_utc: datetime,
              lat: float, lon: float, alt_m: float = 0.0) -> Optional[dict]:
    """ISS Alt/Az + Metadaten (Hoehe ueber Grund, Subpoint, Sonnenstand).
    TLE MUSS uebergeben werden (Injektion); kein stiller Netz-Fallback."""
    try:
        from skyfield.api import EarthSatellite
        sat = EarthSatellite(tle_line1, tle_line2, "ISS", _ts())
        t = _ts().from_datetime(dt_utc.replace(tzinfo=timezone.utc)
                                if dt_utc.tzinfo is None else dt_utc)
        obs = _observer(lat, lon, alt_m)
        topocentric = (sat - obs).at(t)
        alt, az, dist = topocentric.altaz()
        subpoint = sat.at(t).subpoint()
        sun = body_altaz("sun", dt_utc, lat, lon, alt_m) or {}
        return {
            "alt_deg": round(alt.degrees, 4),
            "az_deg": round(az.degrees % 360.0, 4),
            "range_km": round(dist.km, 1),
            "sat_alt_km": round(_sat_height_km(sat, t), 1),
            "subpoint": (round(subpoint.latitude.degrees, 3),
                         round(subpoint.longitude.degrees, 3)),
            "sun_alt_deg": sun.get("alt_deg"),
            # ISS nur sichtbar, wenn Sat helit und Ort dunkel:
            # Sonne am Ort unter -6 Grad (buergerliche Daemmerung vorbei)
            "visibility_window": (alt.degrees > 10.0
                                  and sun.get("alt_deg", 0) < -6.0),
        }
    except Exception:
        return None


def _sat_height_km(sat, t) -> float:
    """Hoehe ueber Grund (Annahherung ueber |r| - Erdradius aequatorial)."""
    import math
    p = sat.at(t).position.km
    return math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2) - 6378.137
