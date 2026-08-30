"""Radiosonden-Plan: regulaere Wetterballon-Aufstiege als klassische
Duenmerungs-Verwechslung. Pure Zeitfenster-Logik, kein Netz.

Standard (WMO-Synoptik): Haupttermine 00 und 12 UTC; der Aufstieg beginnt
typischerweise ~45-60 min VOR dem Termin, die Ballonfahrt dauert ~90-120
min bis ~30-35 km Hoehe. Sichtbar ist der Ballon (mit Radarreflektor /
glitzernder Folie) vor allem in der Duenmerung, wenn er Sonne faengt und
der Boden schon dunkel ist.

Deutsche Radiosonden-Stationen (DWD; Stand Recherche 2026, fuer
Vollstaendigkeit ohne Gewaehr - Engine prueft nur Zeitfenster, keine
Einzelortung):
"""
from __future__ import annotations

from datetime import datetime, timezone

# Hauptaufstiegs-Termine UTC (Stunde, Minute der Freilassung, ca.)
LAUNCHES_UTC = [(0, 0), (12, 0)]
# Zusaetzliche Sommer-/Profilfluege an Kernstationen (unregelmaessig)
EXTRA_LAUNCHES_UTC = [(6, 0), (18, 0)]
ASCENT_LEAD_MIN = 50      # Freilassung erfolgt ~50 min vor dem Termin
FLIGHT_DURATION_MIN = 110  # sichtbare Phase bis Burst/Ende, ca.

STATIONS_DE = {
    "lindenberg": {"lat": 52.21, "lon": 14.12, "wmo": 10393,
                   "extra": True},   # einzige dt. Station mit 4x taeglich
    "meiningen": {"lat": 50.56, "lon": 10.38, "wmo": 10548, "extra": False},
    "essen": {"lat": 51.40, "lon": 6.97, "wmo": 10410, "extra": False},
    "gebhardshagen": {"lat": 49.36, "lon": 7.07, "wmo": 10618, "extra": False},
    "stuttgart": {"lat": 48.83, "lon": 9.20, "wmo": 10738, "extra": False},
    "muechen_uni": {"lat": 48.15, "lon": 11.57, "wmo": 10868, "extra": False},
}


def sonde_in_flight(dt_utc: datetime, station: str) -> bool:
    """War zum Beobachtungszeitpunkt (UTC) ein regulaerer Aufstieg dieser
    Station theoretisch in der Luft? (Zeitfenster-Check, kein Orbit.)
    Tagesgrenzenbewusst: 23:20 UTC liegt im Vorfenster des 00Z-Termins."""
    st = STATIONS_DE.get(station)
    if st is None:
        return False
    launches = list(LAUNCHES_UTC) + (EXTRA_LAUNCHES_UTC if st["extra"] else [])
    minutes = dt_utc.hour * 60 + dt_utc.minute
    for h, m in launches:
        center = h * 60 + m
        for shift in (0, -1440, 1440):   # Fenster des Vor-/Folgetags
            start = center + shift - ASCENT_LEAD_MIN
            end = center + shift + FLIGHT_DURATION_MIN
            if start <= minutes <= end:
                return True
    return False


def any_sonde_in_flight(dt_utc: datetime) -> list[str]:
    return [s for s in STATIONS_DE if sonde_in_flight(dt_utc, s)]
