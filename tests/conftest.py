"""Gemeinsame Fixtures: laedt Crawler + data_sanity aus der WORKSPACE-Kopie
(quasi das, was der naechste Deploy live schaltet) und stellt tmp-DB/State
bereit - keine Netzwerk-Tests, reine Logik."""
import importlib.util
import json
import sys

import pytest

WS = "/home/enigma/.zcode/workspace/default"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def ac():
    """astro_crawler aus der Workspace-Kopie (Import laedt locations.json)."""
    return _load("t_ac", f"{WS}/astro_crawler.py")


@pytest.fixture(scope="session")
def ds():
    return _load("t_ds", f"{WS}/data_sanity.py")


@pytest.fixture()
def isolated(ac, tmp_path, monkeypatch):
    """DB/State/Sanity-State/CSV auf tmp umlenken + frische Initialisierung.
    Gibt das Modul zurueck; Tests arbeiten nie auf Produktivdaten."""
    monkeypatch.setattr(ac, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(ac, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(ac, "DEVIATION_CSV_PATH", str(tmp_path / "dev.csv"))
    ac.db_init()
    return ac


@pytest.fixture()
def rep(ac):
    """SiteReport mit neutralen Werten; Einzeltests ueberschreiben gezielt."""
    def make(**kw):
        r = ac.SiteReport(name=kw.pop("name", "Testort"),
                          lat=kw.pop("lat", 49.47), lon=kw.pop("lon", 8.58))
        for k, v in kw.items():
            setattr(r, k, v)
        return r
    return make
