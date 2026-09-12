"""Observable-Filter: welche Katalogobjekte sind JETZT prinzipiell
sichtbar? Reine Filterung (Hoehe, Grenzgroesse, Nacht) - bewusst KEINE
Empfehlungs-Logik und keine Beobachtungsplanung.

V1-Grenzen (bewusst, kein Bug, siehe docs/EQUIPMENT_INVENTORY.md):
- Filter (UHC/narrowband/Mond) gehen NICHT in die Limiting-Magnitude-
  Berechnung ein - sie verschieben Grenzgroessen nicht linear, sondern
  veraendern den Kontrast je Objekttyp.
- Barlow-Linsen fliessen ausschliesslich als Vergroesserungs-Faktor in
  die Magnification-Korrektur von limiting_magnitude(), falls eine
  konkrete Vergroesserung uebergeben wird - nie als "mehr Oeffnung".

Nutzt skyfield + lokales de421 (identische Basis wie skyfield_source);
Objektkoordinaten aus messier.csv (ra_hours/dec_degrees, J2000).
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .equipment import Equipment
from .limiting_mag import limiting_magnitude

MESSIER_CSV = os.path.expanduser("~/messier.csv")


@dataclass
class SkyObject:
    name: str
    long_name: str
    type: str
    ra_hours: float
    dec_degrees: float
    magnitude: float
    size_arcmin: float


def load_catalog(path: str = MESSIER_CSV) -> list[SkyObject]:
    objs = []
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            try:
                objs.append(SkyObject(
                    name=row["messier"], long_name=row["name"],
                    type=row["type"],
                    ra_hours=float(row["ra_hours"]),
                    dec_degrees=float(row["dec_degrees"]),
                    magnitude=float(row["mag_v"]),
                    size_arcmin=float(row["size_arcmin"])))
            except (KeyError, ValueError):
                continue
    return objs


def _sky_altaz(ra_hours: float, dec_degrees: float, dt_utc: datetime,
               lat: float, lon: float):
    """Alt/Az eines Katalogobjekts (J2000) via skyfield/de421."""
    from skyfield.api import Loader, Star, wgs84
    load = Loader(os.path.expanduser("~/.skyfield"))
    eph = load("de421.bsp")
    ts = load.timescale()
    t = ts.from_datetime(dt_utc if dt_utc.tzinfo
                         else dt_utc.replace(tzinfo=timezone.utc))
    star = Star(ra_hours=ra_hours, dec_degrees=dec_degrees)
    obs = (eph["earth"] + wgs84.latlon(lat, lon)).at(t)
    alt, az, _ = obs.observe(star).apparent().altaz()
    # skyfield liefert numpy-Skalare - FastAPI/JSON braucht native Typen
    return float(alt.degrees), float(az.degrees % 360.0)


def observable_objects(equipment: Equipment, bortle: int,
                       seeing: Optional[float], lat: float, lon: float,
                       when: datetime,
                       min_altitude_deg: float = 30.0,
                       catalog: Optional[list] = None) -> list[dict]:
    """Filtert: Hoehe > min_altitude_deg, Magnitude < Grenzgroesse,
    astronomische Nacht (Sonne < -18 Grad am Ort)."""
    from app.anomaly.sources import skyfield_source as ss
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    m_lim = limiting_magnitude(equipment.aperture_mm, bortle, seeing)
    sun = ss.body_altaz("sun", when, lat, lon) or {}
    is_night = bool(sun.get("alt_deg", 0.0) < -18.0)
    out = []
    for obj in (catalog if catalog is not None else load_catalog()):
        alt, az = _sky_altaz(obj.ra_hours, obj.dec_degrees, when, lat, lon)
        if alt < min_altitude_deg:
            continue
        if obj.magnitude >= m_lim:
            continue
        out.append({
            "name": obj.name, "long_name": obj.long_name, "type": obj.type,
            "magnitude": obj.magnitude, "size_arcmin": obj.size_arcmin,
            "altitude": round(alt, 1), "azimuth": round(az, 1),
            "is_night": is_night,
        })
    return out


def observation_summary(equipment: Equipment, bortle: int,
                        seeing: Optional[float], when: datetime,
                        lat: float, lon: float,
                        min_altitude_deg: float = 30.0,
                        catalog: Optional[list] = None) -> dict:
    objs = observable_objects(equipment, bortle, seeing, lat, lon, when,
                              min_altitude_deg, catalog)
    m_lim = limiting_magnitude(equipment.aperture_mm, bortle, seeing)
    return {
        "limiting_magnitude": m_lim,
        "observable_count": len(objs),
        "objects": objs,
        "filter_applied": {
            "bortle": bortle, "seeing": seeing,
            "aperture_mm": equipment.aperture_mm,
            "min_altitude_deg": min_altitude_deg,
        },
    }
