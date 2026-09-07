import json

import pytest

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
        assert hh >= 18 or hh < 7, f"Tagesstunde {hh} im Nachtraster"


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


# ---------- /api/forecast: id-Lookup, Normalisierung, kein 422 ----------
# TestClient-Tests gegen das Backend-Modul (wie der laufende Dienst import
# es, gleiche Route-Validierung - ein fehlender Parameter wuerde hier als
# 422 auftauchen, genau das Verhalten, das es zu verhindern gilt).

import importlib.util as _ilu
import sys as _sys

_sys.path.insert(0, "/home/enigma/astro-app/backend")  # lpcache-Nachbarmodul


def test_forecast_by_id(forecast_env):
    r = forecast_env.get("/api/forecast", params={"id": "ellerstadt_east"})
    assert r.status_code == 200, r.text
    assert r.json()["marker"] == "ellerstadt"


def test_forecast_by_name(forecast_env):
    r = forecast_env.get("/api/forecast", params={"name": "Ellerstadt Ost"})
    assert r.status_code == 200, r.text
    assert r.json()["marker"] == "ellerstadt"


def test_forecast_name_mit_sonderzeichen_kein_422(forecast_env):
    # Klammern/Kommata im Query: muss 200 (Treffer) oder saubere 404 sein,
    # NIEMALS 422 (Parameter-Validierung)
    r = forecast_env.get("/api/forecast",
                         params={"name": "Ellerstadt Ost (Pfalz)"})
    assert r.status_code in (200, 404), r.text


def test_forecast_slug_form_trifft(forecast_env):
    # 'ellerstadt_ost' (slug) -> normalisiert -> 'Ellerstadt Ost'
    r = forecast_env.get("/api/forecast", params={"name": "ellerstadt_ost"})
    assert r.status_code == 200, r.text


def test_forecast_fehlend_gibt_klare_404(forecast_env):
    r = forecast_env.get("/api/forecast", params={"id": "unbekannt"})
    assert r.status_code == 404 and ("Kein Standort" in r.text
                                     or "Keine Vorausschau" in r.text)


def test_forecast_horizon_konstanten(ac):
    assert ac.FORECAST_HORIZON_HOURS >= 48
    assert ac.FORECAST_FETCH_WINDOW_H == ac.FORECAST_HORIZON_HOURS + 8


# ---------- Bug-Repro 2026-09-04: gemischte Coverages, Mitternachts-Grenze ----------
def test_build_forecast_gemischte_coverages(isolated, rep):
    """co=24 h / om=72 h / seeing=71 h / ground=56 h um 00:01 - exakt das
    Live-Szenario, das ab 02.09. einen TypeError warf (jet-Wert mit
    Regel-None im gemeinsamen K.o.-Block von _hour_score)."""
    import os as _os
    from datetime import datetime as _dt, timedelta as _td
    from unittest.mock import patch as _patch
    import json as _json
    ac = isolated
    NOW = _dt(2026, 9, 2, 0, 1)

    def mk(start, n, base):
        return [{"ts": (start + _td(hours=i)).strftime("%Y-%m-%dT%H:00"),
                 **base} for i in range(n)]

    r = rep()
    r.fc_clouds = mk(_dt(2026, 9, 1, 23), 24,
                     {"total": 30, "low": None, "mid": None, "high": None,
                      "rain": 5})
    r.fc_clouds_src = "clearoutside"
    r.fc_clouds_om = mk(_dt(2026, 9, 1, 23), 72,
                        {"total": 30, "low": 10, "mid": 10, "high": 10,
                         "rain": 5})
    r.fc_seeing = mk(_dt(2026, 9, 1, 23), 71, {"seeing": 1.5, "jet": 20})
    r.fc_ground = mk(_dt(2026, 8, 31, 21), 56,
                     {"cloud": 40, "prob": 10, "wind": 5, "tau": 6,
                      "precip": 0.0})
    r.dark_windows = ["22:00-04:00"]
    r.moon_window = "01:00-04:00"
    r.moon_illum = 60.0

    with _patch.object(ac, "datetime") as DT:
        DT.now.return_value = NOW
        DT.strptime.side_effect = lambda *a: _dt.strptime(*a)
        DT.fromisoformat.side_effect = lambda *a: _dt.fromisoformat(*a)
        ac.build_forecast(r, "dso")   # darf KEINE Exception werfen
    data = _json.load(open(ac.FORECAST_PATH))
    assert r.name in data and data[r.name]["series"], \
        "Forecast muss mit nicht-leerer series geschrieben werden"


# ---------- /api/forecast: Trim verstrichener Stunden + 48h-Kennzeichnung ----------
def _series_from(start, n):
    from datetime import datetime, timedelta
    return [{"ts": (start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00")}
            for i in range(n)]


def test_forecast_trimmt_verstrichene_stunden(forecast_env):
    from datetime import datetime, timedelta
    now = datetime.now()
    fc = forecast_env  # Fixture schreibt Forecast-Datei neu je Test
    # 2 verstrichene + 50 zukuenftige Stunden unter dem Ellerstadt-Key
    import importlib.util, sys, json, os
    be = sys.modules["backend_main"]
    path = be.ac.FORECAST_PATH
    data = json.load(open(path))
    data["Ellerstadt Ost"]["series"] = _series_from(now - timedelta(hours=2), 52)
    json.dump(data, open(path, "w"))
    r = fc.get("/api/forecast", params={"id": "ellerstadt_east"})
    assert r.status_code == 200
    body = r.json()
    cutoff = now.strftime("%Y-%m-%dT%H:00")
    assert all(h["ts"] >= cutoff for h in body["series"]), \
        "kein Eintrag aelter als die aktuelle Stunde"
    assert body["incomplete"] is False
    assert body["forecast_hours_remaining"] >= 47


def test_forecast_kennzeichnet_kurzen_horizont(forecast_env):
    from datetime import datetime, timedelta
    import sys, json
    be = sys.modules["backend_main"]
    path = be.ac.FORECAST_PATH
    data = json.load(open(path))
    data["Ellerstadt Ost"]["series"] = _series_from(datetime.now(), 12)
    json.dump(data, open(path, "w"))
    r = forecast_env.get("/api/forecast", params={"id": "ellerstadt_east"})
    body = r.json()
    assert body["incomplete"] is True
    assert "unvollständig" in body.get("note", "")


# ---------- Golden Window: fester Planungs-Start um 18 Uhr lokal ----------
def _mk_series_forecast(ac, rep, now, dark_window="22:00-04:00"):
    import json as _json
    from datetime import datetime, timedelta
    from unittest.mock import patch
    def mk(start, n, base):
        return [{"ts": (start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00"),
                 **base} for i in range(n)]
    r = rep()
    om_start = now.replace(hour=18, minute=0, second=0, microsecond=0)
    r.fc_clouds_om = mk(om_start, 84,
                        {"total": 10, "low": 0, "mid": 0, "high": 10, "rain": 0})
    r.dark_windows = [dark_window]
    with patch.object(ac, "datetime") as DT:
        DT.now.return_value = now
        DT.strptime.side_effect = lambda *a: datetime.strptime(*a)
        DT.fromisoformat.side_effect = lambda *a: datetime.fromisoformat(*a)
        ac.build_forecast(r, "dso")
    return _json.load(open(ac.FORECAST_PATH))[r.name]


def test_forecast_starts_at_18_local(isolated, rep):
    """Sommer UND Winter: erste Serie-Stunde ist 18:00 lokal (Konstante,
    nicht daemmerungsabhaengig)."""
    from datetime import datetime
    ac = isolated
    for now in (datetime(2026, 7, 21, 15, 0),    # Hochsommer
                datetime(2026, 12, 21, 15, 0)):   # Hochwinter
        data = _mk_series_forecast(ac, rep, now)
        first = data["series"][0]["ts"]
        assert first.endswith("T18:00"), (now.date(), first)
        assert data["series"][0]["night"] == f"{now:%Y-%m-%d}"


def test_bright_hours_appear_in_forecast(isolated, rep):
    """Helle Stunden (18-21 Uhr im Hochsommer) stehen mit dark:false in der
    Liste - Planungsuebersicht, kein Verstecken."""
    from datetime import datetime
    data = _mk_series_forecast(isolated, rep, datetime(2026, 7, 21, 15, 0))
    hours = {h["hhmm"]: h for h in data["series"]}
    for hh in ("18:00", "19:00", "20:00"):
        assert hh in hours, hh
        assert hours[hh]["dark"] is False, hh
        assert hours[hh]["ok"] is False and "hell" in hours[hh]["reasons"], hh


def test_dark_flag_still_correct(isolated, rep):
    """dark:true nur in astronomisch dunklen Stunden - unabhaengig vom
    frueheren Rasterstart (22-04 dunkel, 18-21 hell)."""
    from datetime import datetime
    data = _mk_series_forecast(isolated, rep, datetime(2026, 7, 21, 15, 0))
    dark_hh = {h["hhmm"] for h in data["series"] if h["dark"] is True}
    assert dark_hh <= {"22:00", "23:00", "00:00", "01:00", "02:00",
                       "03:00", "04:00"}, dark_hh
    assert "22:00" in dark_hh and "21:00" not in dark_hh
