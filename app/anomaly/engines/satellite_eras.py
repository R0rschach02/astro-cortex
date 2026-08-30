"""Satelliten-„Epochen“: welche Satelliten-/Konstellations-Typen sind zu
einem Datum ueberhaupt als Erklaerungskandidat zulaessig? Pure Funktionen,
kein Netz, keine Ephemeriden.

Hintergrund: Ein Sichtungsdatum schraenkt die Kandidatenmenge ein - z. B.
gibt es die klassischen Iridium-Flares (spiegelnde Antennen) erst bis ca.
2019 (Nachfolger ohne Flare-Geometrie), Starlink-Trains erst ab 2019,
die ISS durchgehend seit 1998.
"""
from __future__ import annotations

from datetime import date

# Zeitfenster (inkl. Grenzen), in denen ein Erklaerungs muster existierte
SATELLITE_ERAS = [
    # (name, erster Tag, letzter Tag|None=offen, muster-konstante s. signature_check)
    ("iss", date(1998, 11, 20), None),
    ("iridium_flare", date(1997, 1, 1), date(2019, 12, 31)),
    ("starlink_train", date(2019, 5, 24), None),
    ("starlink_single", date(2019, 5, 24), None),
    ("tiangong", date(2021, 4, 29), None),
    ("hubble", date(1990, 4, 25), None),
]


def active_satellite_types(d: date) -> list[str]:
    """Alle Satelliten-Typen, deren Existenzfenster das Datum enthaelt."""
    out = []
    for name, first, last in SATELLITE_ERAS:
        if d >= first and (last is None or d <= last):
            out.append(name)
    return out


def is_plausible(name: str, d: date) -> bool:
    return name in active_satellite_types(d)
