"""Anomalie-Engines: deterministische Regeln, Datenfenster, Ranking."""
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, "/home/enigma")

from app.anomaly.engines import (classifier_rules, meteor_showers,
                                 radiosonde_schedule, satellite_eras,
                                 signature_check)


# ---------- satellite_eras ----------
def test_iss_aktiv_seit_1998():
    assert satellite_eras.is_plausible("iss", date(2005, 6, 1))
    assert satellite_eras.is_plausible("iss", date(2026, 8, 30))


def test_iridium_nach_2019_aus():
    assert satellite_eras.is_plausible("iridium_flare", date(2015, 3, 1))
    assert not satellite_eras.is_plausible("iridium_flare", date(2021, 1, 1))


def test_starlink_vor_2019_unmoeglich():
    assert not satellite_eras.is_plausible("starlink_train", date(2018, 1, 1))
    assert satellite_eras.is_plausible("starlink_train", date(2020, 1, 1))


# ---------- meteor_showers ----------
def test_perseiden_im_august_aktiv():
    assert "perseiden" in meteor_showers.active_showers(date(2026, 8, 12))
    assert meteor_showers.is_peak("perseiden", date(2026, 8, 12))


def test_geminiden_im_august_unmoeglich():
    assert "geminiden" not in meteor_showers.active_showers(date(2026, 8, 12))


def test_radiant_format():
    ra, dec = meteor_showers.radiant("geminiden")
    assert 0 <= ra < 360 and -90 <= dec <= 90


# ---------- radiosonde_schedule ----------
def test_haupttermin_00utc_lindenberg():
    # 23:20 UTC = 20 min vor 00Z: Aufstieg laeuft
    assert radiosonde_schedule.sonde_in_flight(
        datetime(2026, 8, 30, 23, 20, tzinfo=timezone.utc), "lindenberg")
    assert radiosonde_schedule.sonde_in_flight(
        datetime(2026, 8, 30, 11, 30, tzinfo=timezone.utc), "meiningen")


def test_nachmittag_kein_regulaerer_aufstieg():
    # 15:00 UTC liegt zwischen 12Z-Ende (~14:00) und 23:10-Vorbereitung
    assert not radiosonde_schedule.sonde_in_flight(
        datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc), "meiningen")


def test_nur_lindenberg_hat_extra_termine():
    t = datetime(2026, 8, 30, 17, 30, tzinfo=timezone.utc)   # 18Z-Fenster
    active = radiosonde_schedule.any_sonde_in_flight(t)
    assert "lindenberg" in active and "meiningen" not in active


# ---------- signature_check ----------
def test_meteor_signatur_passt_zu_meteor_beobachtung():
    obs = {"duration_s": 4, "brightness": "hell", "movement": "plotzlich",
           "color": "weiss"}
    assert signature_check.signature_compatible(obs, "meteor")
    assert "iss" not in signature_check.compatible_signatures(obs)


def test_lange_beobachtung_schliesst_meteor_aus():
    obs = {"duration_s": 600}
    assert not signature_check.signature_compatible(obs, "meteor")
    assert signature_check.signature_compatible(obs, "venus")


def test_fehlende_angaben_schliessen_nichts_aus():
    assert signature_check.signature_compatible({}, "iss")


# ---------- classifier_rules ----------
def test_rank_vollstaendig_gestuetzt():
    ranked = classifier_rules.rank_candidates({
        "iss": {"era": True, "time": None, "signature": True, "position": True},
        "meteor": {"era": None, "time": True, "signature": False,
                   "position": None}})
    assert ranked[0] == ("iss", 1.0)
    scores = [s for _, s in ranked]
    assert 0.0 <= min(scores) and max(scores) <= 1.0


def test_position_wiegt_doppelt():
    ranked = classifier_rules.rank_candidates({
        "a": {"position": True, "signature": False},   # 2/3
        "b": {"position": False, "signature": True},   # 1/3
    })
    assert ranked[0][0] == "a" and abs(ranked[0][1] - 2 / 3) < 0.002


def test_geipan_vorschlaege():
    assert classifier_rules.suggest_geipan([("iss", 0.95)], "gut") == "A"
    assert classifier_rules.suggest_geipan([("iss", 0.7)], "gut") == "B"
    assert classifier_rules.suggest_geipan([("iss", 0.95)], "duenn") == "C"
    assert classifier_rules.suggest_geipan([("?", 0.2)], "gut") == "D"
