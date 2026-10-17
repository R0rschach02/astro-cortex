"""v1.1.13 Seeing-Hoehenspreizung: Rohe API-Werte muessen nach Elevation
gespreizt werden - Odenwald-Berge deutlich besser, Rheingraben/Urban
deutlich schlechter als der Referenzwert (150 m / Weinheim)."""
import sys

sys.path.insert(0, "/home/enigma")
sys.path.insert(0, "/home/enigma/astro-app/backend")

from main import _seeing_elevation_adjusted as adj   # noqa: E402

KATZENBUCKEL = {"name": "Katzenbuckel", "elevation_m": 626,
                "bortle_class": 4}
KOENIGSSTUHL = {"name": "Koenigsstuhl", "elevation_m": 550,
                "bortle_class": 4}
WEINHEIM = {"name": "Weinheim", "elevation_m": 150, "bortle_class": 6}
MANNHEIM = {"name": "Mannheim", "elevation_m": 95, "bortle_class": 7}
HEMSBACH = {"name": "Hemsbach", "elevation_m": 120, "bortle_class": 6}


def test_none_durchgereicht():
    assert adj(WEINHEIM, None) == (None, None)


def test_referenz_150m_unchanged():
    val, f = adj(WEINHEIM, 1.6)
    assert val == 1.6 and f == 1.0


def test_hoehen_bonus_odenwald():
    val, f = adj(KATZENBUCKEL, 1.6)
    assert val < 1.1 and f < 0.65, (val, f)
    val2, _ = adj(KOENIGSSTUHL, 1.6)
    assert 1.0 < val2 < 1.1, val2
    assert val < val2  # hoeher = besser


def test_urban_penalty_graben():
    val, f = adj(MANNHEIM, 1.6)
    assert val > 2.1 and f > 1.35, (val, f)
    val_h, f_h = adj(HEMSBACH, 1.6)   # 120m aber Bortle 6: nur Hoehenanteil
    assert 1.65 < val_h < 1.85 and 1.0 < f_h < 1.15


def test_spreizung_monoton_nach_hoehe():
    vals = [adj(l, 1.6)[0] for l in
            (MANNHEIM, HEMSBACH, WEINHEIM, KOENIGSSTUHL, KATZENBUCKEL)]
    assert vals == sorted(vals, reverse=True), vals


def test_klemmen():
    assert adj(KATZENBUCKEL, 0.1)[0] == 0.4   # Floor
    assert adj(MANNHEIM, 10.0)[0] == 5.0      # Ceiling
    # roh None / ungueltig -> None
    assert adj(KATZENBUCKEL, "kaputt") == (None, None)
