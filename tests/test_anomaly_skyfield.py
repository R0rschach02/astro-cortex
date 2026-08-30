"""Skyfield-Source gegen JPL-Horizons-Ground-Truth (Fixtures) und
physikalische ISS-Invarianten. Kein Netz noetig: de421 lokal, TLE als
Fixture (Epoche im Fixture-Dokument vermerkt)."""
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/enigma")

from app.anomaly.sources import skyfield_source as ss

FIX = "/home/enigma/tests/fixtures/horizons_altaz_20260830.json"
TLE = "/home/enigma/tests/fixtures/iss_tle.txt"

# de421 vs. Horizons (JPL-Modern-Ephemeriden): tolerated Abweichung.
# Gemessene Abweichung: < 0.001 Grad; Toleranz bewusst grosszuegiger.
TOL_DEG = 0.05


def test_bodies_against_horizons():
    fx = json.load(open(FIX))
    dt = datetime(2026, 8, 30, 21, 0, tzinfo=timezone.utc)
    ob = fx["observer"]
    for body, ref in fx["positions"].items():
        got = ss.body_altaz(body, dt, ob["lat"], ob["lon"], ob["alt_m"])
        assert got is not None, body
        daz = min(abs(got["az_deg"] - ref["az_deg"]),
                  360 - abs(got["az_deg"] - ref["az_deg"]))
        dalt = abs(got["alt_deg"] - ref["alt_deg"])
        assert daz < TOL_DEG and dalt < TOL_DEG, \
            f"{body}: az-diff {daz:.4f}, alt-diff {dalt:.4f}"


def test_sonne_unter_horizont_nachts():
    dt = datetime(2026, 8, 30, 21, 0, tzinfo=timezone.utc)
    r = ss.body_altaz("sun", dt, 49.5, 8.6)
    assert r["alt_deg"] < -18   # astronomische Nacht am Fixture-Zeitpunkt


def test_iss_physikalische_invarianten():
    lines = [l.strip() for l in open(TLE) if l.strip()]
    assert lines[1].startswith("1 25544"), "Fixture ist kein ISS-TLE"
    assert lines[2].startswith("2 25544"), "Fixture ist kein ISS-TLE"
    dt = datetime(2026, 8, 30, 21, 0, tzinfo=timezone.utc)
    r = ss.iss_altaz(lines[1], lines[2], dt, 49.5, 8.6)
    assert r is not None
    assert 350 <= r["sat_alt_km"] <= 470        # ISS-Flughoehe
    assert r["range_km"] < 14000                # Sichtweite max ~1/3 Erdumfang
    assert -90 <= r["alt_deg"] <= 90 and 0 <= r["az_deg"] < 360
    assert -90 <= r["subpoint"][0] <= 90 and -180 <= r["subpoint"][1] <= 180


def test_unbekannter_koerper_liefert_none():
    assert ss.body_altaz("alpha_centauri",
                         datetime(2026, 8, 30, tzinfo=timezone.utc),
                         49.5, 8.6) is None


def test_property_positions_stetig(hypothesis=False):
    """Venus-Position aendert sich pro Minute minimal (property-artig,
    ohne hypothesis-Abhaengigkeit im Deploy-Gate)."""
    t0 = datetime(2026, 8, 30, 21, 0, tzinfo=timezone.utc)
    for minutes in (1, 5, 30):
        from datetime import timedelta
        r0 = ss.body_altaz("venus", t0, 49.5, 8.6)
        r1 = ss.body_altaz("venus", t0 + timedelta(minutes=minutes),
                           49.5, 8.6)
        assert abs(r1["alt_deg"] - r0["alt_deg"]) < 0.5 * minutes
        assert abs(r1["az_deg"] - r0["az_deg"]) < 0.5 * minutes
