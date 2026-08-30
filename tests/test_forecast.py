"""Forecast-Bausteine: Zeitfenster-Logik + build_forecast-Smoke."""


def test_fenster_innerhalb(ac):
    assert ac._hh_in_window("23:00", "22:00-04:00") is True
    assert ac._hh_in_window("03:30", "22:00-04:00") is True


def test_fenster_ausserhalb(ac):
    assert ac._hh_in_window("12:00", "22:00-04:00") is False
    assert ac._hh_in_window("21:59", "22:00-04:00") is False


def test_fenster_ohne_mitternacht(ac):
    assert ac._hh_in_window("20:30", "20:00-23:00") is True
    assert ac._hh_in_window("19:59", "20:00-23:00") is False


def test_fenster_degenerate(ac):
    assert ac._hh_in_window("10:00", None) is False
    assert ac._hh_in_window("10:00", "kaputt") is False


def test_build_forecast_smoke(isolated, rep):
    """Minimalbesetzter Report: kein Crash, latest-wins-Datei entsteht,
    Nachtraster enthaelt nur Stunden 20-07."""
    import json
    import os
    from datetime import datetime, timedelta
    ac = isolated
    now = datetime.now()
    r = rep(clouds_total=10, seeing=1.0)
    r.fc_clouds = [{"ts": (now + timedelta(hours=h)).isoformat(timespec="minutes"),
                    "total": 10 + h, "low": None, "mid": None, "high": None,
                    "rain": 5} for h in range(24)]
    r.fc_clouds_src = "clearoutside"
    r.dark_windows = ["22:00-04:00"]
    ac.build_forecast(r, "dso")
    assert os.path.exists(ac.FORECAST_PATH)
    data = json.load(open(ac.FORECAST_PATH))
    assert r.name in data
    hours = data[r.name].get("series") or data[r.name].get("hours") or []
    for h in hours:
        hh = int((h.get("ts") or h.get("hhmm", "00:00"))[11:13])
        assert hh >= 20 or hh < 7, f"Tagesstunde {hh} im Nachtraster"


# ---------- _hour_score (nach PROFILE_RULES-Umstellung) ----------

def test_hour_score_dso_hell_ist_ko(ac):
    ok, why = ac._hour_score({"dark": False, "clouds": 5}, "dso")
    assert ok is False and why == ["hell"]


def test_hour_score_planet_daemmerung_ok(ac):
    ok, _ = ac._hour_score({"dark": False, "clouds": 10, "seeing": 1.2}, "planet")
    assert ok is True


def test_hour_score_dso_mond_hoch_ko(ac):
    ok, why = ac._hour_score({"dark": True, "clouds": 5, "seeing": 0.8,
                              "moon_up": True, "moon_illum": 90}, "dso")
    assert ok is False and "Mond" in why[0]


def test_hour_score_grenzwerte_wolken(ac):
    assert ac._hour_score({"dark": True, "clouds": 41}, "dso")[0] is False
    assert ac._hour_score({"dark": True, "clouds": 40}, "dso")[0] is True
    assert ac._hour_score({"dark": False, "clouds": 51}, "planet")[0] is False
    ok, _ = ac._hour_score({"dark": False, "clouds": 30}, "planet")
    assert ok is True
