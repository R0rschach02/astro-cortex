"""Deployment-Logik: Dynamic Abort verlegt die Rueckfahrt vor."""
import os
import sys
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, "/home/enigma")

from app.engine.deployment import deployment_window
from app.sources.transit import GTFSStaticSource

SERVICE = date(2026, 9, 14)


@pytest.fixture(scope="module")
def gtfs_dir(tmp_path_factory):
    """Mini-Netz: HOME H1 --S1--> MIT H2 --BUS--> ZIEL H3.
    Rueckweg: BUS-RET H3 23:50 -> H2 00:10, S1-RET H2 00:20 -> H1 00:30
    (nach Mitternacht, GTFS 24:xx-Kodierung)."""
    d = tmp_path_factory.mktemp("gtfs")
    (d / "stops.txt").write_text(
        "stop_id,stop_name,stop_lat,stop_lon,location_type\n"
        "H1,Wohnort Bhf,49.49250,8.51005,0\n"
        "H2,Mitte,49.48005,8.56005,0\n"
        "H3,Ziel Ort,49.46220,8.26785,0\n", encoding="utf-8")
    (d / "routes.txt").write_text(
        "route_id,route_short_name,route_long_name\n"
        "R_S1,S1,\nR_BUS,Bus 421,\n", encoding="utf-8")
    (d / "trips.txt").write_text(
        "route_id,service_id,trip_id\n"
        "R_S1,S1,S1_hin\nR_BUS,S1,BUS_hin\n"
        "R_BUS,S1,BUS_ret\nR_S1,S1,S1_ret\n", encoding="utf-8")
    (d / "calendar.txt").write_text(
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,"
        "sunday,start_date,end_date\n"
        "S1,1,1,1,1,1,1,1,20260101,20261231\n", encoding="utf-8")
    (d / "stop_times.txt").write_text(
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        # Hin: S1 22:00 H1 -> 22:10 H2; Bus 22:20 H2 -> 22:40 H3
        "S1_hin,22:00:00,22:00:00,H1,1\n"
        "S1_hin,22:10:00,22:10:00,H2,2\n"
        "BUS_hin,22:20:00,22:20:00,H2,1\n"
        "BUS_hin,22:40:00,22:40:00,H3,2\n"
        # Rueck nach Mitternacht: 24:50 = 00:50, 25:10 = 01:10
        "BUS_ret,24:50:00,24:50:00,H3,1\n"
        "BUS_ret,25:10:00,25:10:00,H2,2\n"
        "S1_ret,25:20:00,25:20:00,H2,1\n"
        "S1_ret,25:30:00,25:30:00,H1,2\n", encoding="utf-8")
    return str(d)


HOME = {"name": "Home", "lat": 49.4900, "lon": 8.5100}
OBS = {"name": "Ziel", "lat": 49.4646, "lon": 8.2678}
GWS = datetime(2026, 9, 14, 23, 0)
GWE = datetime(2026, 9, 15, 1, 0)   # 01:00 = naechster Tag


def _plan(gtfs_dir, **kw):
    src = GTFSStaticSource(gtfs_dir, service_date=SERVICE)
    return deployment_window(GWS, GWE, HOME, OBS, src,
                             setup_minutes=15, **kw)


def test_normal_rueckfahrt_nach_fensterende(gtfs_dir):
    plan = _plan(gtfs_dir)
    assert plan.latest_departure["time"] == "22:00"
    # Rueckfahrt ab Fensterende 01:00 -> erste Nacht-Verbindung ist die
    # 24:50-Bus (00:50)? Nein: 00:50 < 01:00 -> erst S1_ret 01:20 ab H2
    # gilt nicht (Start H3) -> BUS_ret 00:50 liegt VOR Fensterende und
    # faellt raus. Erste gueltige Rueckfahrt: keine im Feed -> leer.
    assert plan.extraction is None
    assert plan.dynamic_abort is None


def test_dynamic_abort_verlegt_rueckfahrt_vor(gtfs_dir):
    # Wetterumschlag um 00:30 (laufendes Fenster): Rueckfahrt-Suche ab
    # 00:30 findet die 00:50-Bus (vorher durch 01:00-Fensterende
    # ausgeschlossen) + Anschluss S1 01:20 -> Home 01:30.
    abort_at = datetime(2026, 9, 15, 0, 30)
    plan = _plan(gtfs_dir, abort_at=abort_at)
    assert plan.dynamic_abort == {"time": "00:30"}
    assert plan.extraction is not None
    assert plan.extraction["earliest_return"] == "00:50"
    assert plan.extraction["arrival_home"] == "01:30"
    # Abbau-Warnung 30 min vor Rueckfahrt
    assert plan.extraction_warning_ts == "00:20"


def test_hinfahrt_mit_steps_pills(gtfs_dir):
    plan = _plan(gtfs_dir)
    steps = plan.latest_departure["steps"]
    kinds = [s["kind"] for s in steps]
    # Umstieg H2 -> H2 gleicher Stop = kein Fussweg; nur Start/Ziel-Walks
    assert kinds == ["walk", "ride", "ride", "walk"]
    rides = [s for s in steps if s["kind"] == "ride"]
    assert [r["line"] for r in rides] == ["S1", "Bus 421"]
    assert rides[0]["dep"] == "22:00" and rides[0]["arr"] == "22:10"
