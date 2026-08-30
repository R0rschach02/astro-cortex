"""Property-based Tests (Hypothesis) fuer die deterministischen Engines.
Hypothesis ist optional: ohne Installation werden diese Tests uebersprungen
(importorskip) - das Deploy-Gate haengt nie an einem optionalen Paket."""
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, "/home/enigma")

from app.anomaly.engines import (classifier_rules, meteor_showers,
                                 radiosonde_schedule, satellite_eras,
                                 signature_check)

st_dates = st.dates(min_value=date(1990, 1, 1), max_value=date(2030, 12, 31))


@settings(max_examples=200, deadline=None)
@given(d=st_dates)
def test_property_era_aktiviert_nur_im_fenster(d):
    for name, first, last in satellite_eras.SATELLITE_ERAS:
        aktiv = satellite_eras.is_plausible(name, d)
        im_fenster = d >= first and (last is None or d <= last)
        assert aktiv == im_fenster, name


@settings(max_examples=200, deadline=None)
@given(d=st_dates)
def test_property_peaktag_ist_aktiv(d):
    for name in meteor_showers.SHOWERS:
        peak = meteor_showers.peak_day(name)
        probe = date(d.year, *peak)
        if probe.year == d.year:
            assert name in meteor_showers.active_showers(probe), \
                f"{name}: Peaktag {probe} muss im Aktivitaetsfenster liegen"


@settings(max_examples=100, deadline=None)
@given(h=st.integers(0, 23), m=st.integers(0, 59),
       station=st.sampled_from(list(radiosonde_schedule.STATIONS_DE)))
def test_property_sonde_nur_nahe_der_termine(h, m, station):
    dt = datetime(2026, 8, 30, h, m, tzinfo=timezone.utc)
    minutes = h * 60 + m
    ok = radiosonde_schedule.sonde_in_flight(dt, station)
    launches = list(radiosonde_schedule.LAUNCHES_UTC)
    if radiosonde_schedule.STATIONS_DE[station]["extra"]:
        launches += radiosonde_schedule.EXTRA_LAUNCHES_UTC
    # zirkulaerer Minutenabstand (Tagesgrenze: 23:xx gehoert zu 00Z)
    def near(lh, lm):
        d = (minutes - (lh * 60 + lm)) % 1440
        return d <= radiosonde_schedule.FLIGHT_DURATION_MIN \
            or d >= 1440 - radiosonde_schedule.ASCENT_LEAD_MIN
    im_fenster = any(near(lh, lm) for lh, lm in launches)
    assert ok == im_fenster, station


@settings(max_examples=150, deadline=None)
@given(name=st.sampled_from(list(signature_check.SIGNATURES)))
def test_property_signatur_ist_mit_sich_selbst_kompatibel(name):
    sig = signature_check.SIGNATURES[name]
    obs = {"duration_s": (sig["duration_s"][0] + sig["duration_s"][1]) / 2,
           "brightness": sig["brightness"],
           "movement": sig["movement"],
           "color": sorted(sig["colors"])[0]}
    assert signature_check.signature_compatible(obs, name)


@settings(max_examples=100, deadline=None)
@given(cands=st.dictionaries(
    st.sampled_from(["iss", "meteor", "venus", "radiosonde"]),
    st.fixed_dictionaries({"era": st.none() | st.booleans(),
                           "time": st.none() | st.booleans(),
                           "signature": st.none() | st.booleans(),
                           "position": st.none() | st.booleans()})))
def test_property_rank_scores_in_0_1(cands):
    ranked = classifier_rules.rank_candidates(cands)
    for _, score in ranked:
        assert 0.0 <= score <= 1.0
    # Sortierung absteigend
    scores = [s for _, s in ranked]
    assert scores == sorted(scores, reverse=True)
