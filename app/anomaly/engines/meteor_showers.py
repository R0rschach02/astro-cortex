"""Meteorstroeme: harte Peaks und Aktivitaetsfenster (Jahres-Muster, nach
IMO-ueblichen Werten gerundet; hardcodiert und bewusst konservativ - ein
Sichtung ausserhalb des Fensters kann kein Strohm-Meteor gewesen sein).
Pure Daten, keine Praezisionsephemeride: Radiant-Koordinaten genuegen fuer
eine Plausibilitaetsbewertung (war der Radiant ueber dem Horizont?).
"""
from __future__ import annotations

# name: (Peak m/d, Aktivitaetsfenster m/d, Radiant ra_deg, radiant_dec_deg, max_zenitalrate)
SHOWERS = {
    "quadrantiden": ((1, 3), ((1, 1), (1, 5)), 230.0, 49.0, 110),
    "lyriden": ((4, 22), ((4, 16), (4, 25)), 271.0, 34.0, 18),
    "eta_aquariiden": ((5, 5), ((4, 19), (5, 28)), 338.0, -1.0, 50),
    "delta_aquariiden_sued": ((7, 30), ((7, 12), (8, 23)), 340.0, -16.0, 25),
    "perseiden": ((8, 12), ((7, 17), (8, 24)), 48.0, 58.0, 100),
    "orioniden": ((10, 21), ((10, 2), (11, 7)), 95.0, 16.0, 20),
    "suedliche_tauriden": ((11, 5), ((10, 20), (11, 30)), 58.0, 22.0, 7),
    "nordliche_tauride": ((11, 12), ((10, 20), (12, 10)), 58.0, 22.0, 7),
    "leoniden": ((11, 17), ((11, 6), (11, 30)), 152.0, 22.0, 15),
    "geminiden": ((12, 14), ((12, 4), (12, 20)), 112.0, 33.0, 150),
    "ursiden": ((12, 22), ((12, 17), (12, 26)), 217.0, 76.0, 10),
}


def _md(d) -> tuple:
    return (d.month, d.day)


def active_showers(d) -> list[str]:
    """Stroeme, deren Aktivitaetsfenster das Datum enthaelt."""
    md = _md(d)
    out = []
    for name, (_peak, (lo, hi), _ra, _dec, _zhr) in SHOWERS.items():
        in_win = (lo <= md <= hi) if lo <= hi else (md >= lo or md <= hi)
        if in_win:
            out.append(name)
    return out


def peak_day(name: str):
    return SHOWERS[name][0]


def radiant(name: str) -> tuple:
    """(ra_deg, dec_deg) des Radianten."""
    return (SHOWERS[name][2], SHOWERS[name][3])


def is_peak(name: str, d) -> bool:
    return _md(d) == peak_day(name)
