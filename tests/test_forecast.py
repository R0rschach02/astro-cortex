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


# ---------- /api/forecast: id-Lookup, Normalisierung, kein 422 ----------
# TestClient-Tests gegen das Backend-Modul (wie der laufende Dienst import
# es, gleiche Route-Validierung - ein fehlender Parameter wuerde hier als
# 422 auftauchen, genau das Verhalten, das es zu verhindern gilt).

import importlib.util as _ilu
import sys as _sys

_sys.path.insert(0, "/home/enigma/astro-app/backend")  # lpcache-Nachbarmodul


@pytest.fixture(scope="module")
def backend(ac, tmp_path_factory):
    spec = _ilu.spec_from_file_location(
        "backend_main", "/home/enigma/astro-app/backend/main.py")
    mod = _ilu.module_from_spec(spec)
    _sys.modules["backend_main"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def forecast_env(backend, ac, tmp_path, monkeypatch):
    """Forecast-JSON + Locations auf Testdaten umbiegen."""
    fc = tmp_path / "forecast.json"
    fc.write_text(json.dumps({
        "Ellerstadt Ost": {"nights": [], "marker": "ellerstadt"},
        "Mannheim Neckarplatten": {"nights": [], "marker": "mannheim"},
    }))
    monkeypatch.setattr(backend.ac, "FORECAST_PATH", str(fc))
    monkeypatch.setattr(
        backend.ac, "DEFAULT_LOCATIONS",
        [{"id": "ellerstadt_east", "name": "Ellerstadt Ost",
          "lat": 49.4645591, "lon": 8.2677846}])
    monkeypatch.setattr(backend.ac, "active_locations", lambda d: d)
    monkeypatch.setattr(backend.ac, "load_watchlist", lambda: [])
    from fastapi.testclient import TestClient
    return TestClient(backend.app)


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
