"""Equipment/Limiting-Magnitude/Observable-Filter: Zielwerte, Filterregeln,
Endpoint."""
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, "/home/enigma")

from app.engine import equipment as eq_mod
from app.engine import limiting_mag as lm
from app.engine import observable_filter as of


# ---------- limiting_magnitude (Zielwerte des Auftrags) ----------
def test_limiting_magnitude():
    m = lm.limiting_magnitude(150, 5, 1.3)
    assert 12.0 <= m <= 12.5, m


def test_limiting_magnitude_dark_sky():
    m = lm.limiting_magnitude(150, 1, 1.0)
    assert m >= 14.0, m


def test_limiting_magnitude_light_pollution():
    m = lm.limiting_magnitude(150, 8, None)
    assert 10.0 <= m <= 10.8, m


def test_limiting_magnitude_monoton_in_bortle_und_seeing():
    prev = 99
    for k in range(1, 10):
        m = lm.limiting_magnitude(150, k, 1.0)
        assert m <= prev
        prev = m
    assert lm.limiting_magnitude(150, 5, 4.0) \
        < lm.limiting_magnitude(150, 5, 2.0)
    assert lm.limiting_magnitude(150, 5, 1.0, magnification=300) \
        > lm.limiting_magnitude(150, 5, 1.0, magnification=30)


def test_limiting_magnitude_rejects_invalid_aperture():
    with pytest.raises(ValueError):
        lm.limiting_magnitude(0, 5, 1.0)


# ---------- Equipment-Lader + Rollen ----------
def test_equipment_loader_und_guiding_rolle():
    inv = eq_mod.load_equipment()
    assert "newton_150" in inv["telescopes"]
    assert set(inv) >= {"telescopes", "cameras", "eyepieces",
                        "barlow_lenses", "filters"}
    eq = eq_mod.build_equipment(inv, "newton_150", "asi120mc_s")
    assert eq.aperture_mm == 150
    assert eq.camera_role == "guiding"   # Inventar ja, Optik nein


def test_equipment_unbekanntes_teleskop():
    inv = eq_mod.load_equipment()
    with pytest.raises(KeyError):
        eq_mod.build_equipment(inv, "nonexistent")


# ---------- observable_filter ----------
@pytest.fixture(scope="module")
def catalog():
    return of.load_catalog()


def test_catalog_vollstaendig_mit_mag_und_size(catalog):
    assert len(catalog) == 110
    assert all(-1 <= o.magnitude <= 12 for o in catalog)
    assert all(0.5 <= o.size_arcmin <= 900 for o in catalog)
    m31 = next(o for o in catalog if o.name == "M31")
    assert m31.magnitude == 3.4 and m31.size_arcmin == 178


def test_observable_filter_nur_ueber_horizont(catalog):
    eq = eq_mod.build_equipment(eq_mod.load_equipment(), "newton_150")
    objs = of.observable_objects(
        eq, 5, 1.3, 49.4645591, 8.2677846,
        when=datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc),
        min_altitude_deg=30.0, catalog=catalog)
    assert objs, "Nachthimmel sollte Objekte ueber 30 Grad haben"
    assert all(o["altitude"] > 30.0 for o in objs)
    # Kontrolle: strengeres Mindest-Hoehenlimit schneidet weiter ab
    stricter = of.observable_objects(
        eq, 5, 1.3, 49.4645591, 8.2677846,
        when=datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc),
        min_altitude_deg=60.0, catalog=catalog)
    assert len(stricter) < len(objs)


def test_observable_filter_magnitude(catalog):
    eq = eq_mod.build_equipment(eq_mod.load_equipment(), "newton_150")
    m_lim = lm.limiting_magnitude(150, 5, 1.3)
    objs = of.observable_objects(
        eq, 5, 1.3, 49.4645591, 8.2677846,
        when=datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc),
        catalog=catalog)
    assert all(o["magnitude"] < m_lim for o in objs)
    # Bortle 8 (Stadt) muss weniger Objekte liefern als Bortle 5
    city = of.observable_objects(
        eq, 8, 1.3, 49.4645591, 8.2677846,
        when=datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc),
        catalog=catalog)
    assert len(city) < len(objs)


# ---------- Endpoint ----------
@pytest.fixture()
def observable_env(monkeypatch, tmp_path):
    import importlib.util as ilu
    import json as _json
    spec = ilu.spec_from_file_location(
        "backend_main_obs", "/home/enigma/astro-app/backend/main.py")
    mod = ilu.module_from_spec(spec)
    sys.modules["backend_main_obs"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(
        mod.ac, "DEFAULT_LOCATIONS",
        [{"id": "ellerstadt_east", "name": "Ellerstadt Ost",
          "lat": 49.4645591, "lon": 8.2677846, "bortle_class": 5}])
    monkeypatch.setattr(mod.ac, "active_locations", lambda d: d)
    monkeypatch.setattr(mod.ac, "load_watchlist", lambda: [])
    return mod


def test_observable_endpoint(observable_env):
    from fastapi.testclient import TestClient
    client = TestClient(observable_env.app)
    r = client.get("/api/observable",
                   params={"id": "ellerstadt_east",
                           "equipment": "newton_150",
                           "seeing": 1.3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert 11.5 <= body["limiting_magnitude"] <= 12.5
    assert body["observable_count"] == len(body["objects"])
    assert body["filter_applied"]["bortle"] == 5
    assert all(o["altitude"] > 30 for o in body["objects"])


def test_observable_endpoint_fehlerfaelle(observable_env):
    from fastapi.testclient import TestClient
    client = TestClient(observable_env.app)
    assert client.get("/api/observable",
                      params={"id": "gibtsnicht",
                              "equipment": "newton_150"}
                      ).status_code == 404
    assert client.get("/api/observable",
                      params={"id": "ellerstadt_east",
                              "equipment": "refraktor_100"}
                      ).status_code == 400
