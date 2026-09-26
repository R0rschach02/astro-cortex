"""Libration + Kolongitude: Pflicht-Validierung gegen den klassischen
Referenzfall aus XEphem libastro/mooncolong.c (Bruning & Talcott,
Astronomy 10/1995): JD 2449992.5 muss Kolongitude 3.69 Grad geben.
XEphems eigene Serie liefert dort 3.686, DE421+PA-Kernel 3.741 -
Toleranz 0.1 Grad deckt die Genauigkeit der Magazin-Reihe ab."""
import os
import sys

import pytest

sys.path.insert(0, "/home/enigma")

from astro_crawler import SKYFIELD_DIR, _libration_colong_state


@pytest.fixture(scope="module")
def ts():
    from skyfield.api import Loader
    return Loader(SKYFIELD_DIR).timescale()


@pytest.mark.skipif(not all(os.path.exists(os.path.join(
        SKYFIELD_DIR, f)) for f in
        ("moon_080317.tf", "pck00008.tpc",
         "moon_pa_de421_1900-2050.bpc")),
    reason="NAIF-Kernel nicht heruntergeladen")
def test_kolongitude_referenzfall_xephem(ts):
    st = _libration_colong_state(ts.ut1_jd(2449992.5))
    assert st is not None
    assert abs(st["colong"] - 3.69) < 0.1, st


def test_kolongitude_tagesrate_plausibel(ts):
    """Kolongitude wandert ~12.19 Grad/Tag - zwei Werte 24 h auseinander
    muessen ~12 Grad Abstand haben (mod 360)."""
    a = _libration_colong_state(ts.ut1_jd(2449992.5))
    b = _libration_colong_state(ts.ut1_jd(2449993.5))
    if a is None or b is None:
        pytest.skip("Kernel fehlen")
    d = abs(b["colong"] - a["colong"]) % 360
    d = min(d, 360 - d)
    assert 11.5 < d < 12.9, (a, b)


def test_libration_wertebereich(ts):
    st = _libration_colong_state(ts.ut1_jd(2449992.5))
    if st is None:
        pytest.skip("Kernel fehlen")
    assert -90 <= st["lib_b"] <= 90
    assert -180 <= st["lib_l"] <= 180
