"""Transit-Source: Router auf synthetischem Mini-GTFS (nicht der
Deutschland-Feed). Determinismus, Umstiege, Fusswege, Fehlerfaelle."""
import asyncio
import os
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, "/home/enigma")

from app.sources.transit import (GTFSNotAvailableError, GTFSStaticSource,
                                 WALK_SPEED_M_PER_MIN)

# Mini-Netz (ca. 1 km-Raster um Mannheim herum):
#   HOME (49.4900, 8.5100)   -> Haltestelle H1 "Wohnort Bhf" (S1)
#   MIT  (49.4800, 8.5600)   -> Haltestelle H2 "Mitte" (S1 + Bus 421)
#   ZIEL (49.4646, 8.2678)   -> Haltestelle H3 "Ellerstadt Ort" (Bus 421)
# Linien: S1  H1 23:00 -> H2 23:10
#         B421 H2 23:20 -> H3 23:40   (1 Umstieg, Ankunft 23:40)
#         RE H1 23:30 -> H3 23:45     (Direkt, spaeter)
SERVICE_DATE = date(2026, 9, 14)


@pytest.fixture(scope="module")
def gtfs_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("gtfs")
    (d / "stops.txt").write_text(
        "stop_id,stop_name,stop_lat,stop_lon,location_type\n"
        "H1,Wohnort Bhf,49.49005,8.51005,0\n"     # ~5-8 m neben HOME
        "H2,Mitte,49.48005,8.56005,0\n"
        "H3,Ellerstadt Ort,49.46465,8.26785,0\n"
        "STATION,Ellerstadt Station,49.4640,8.2670,1\n", encoding="utf-8")
    (d / "routes.txt").write_text(
        "route_id,route_short_name,route_long_name\n"
        "R_S1,S1,\nR_BUS,Bus 421,\nR_RE,RE 4,\n", encoding="utf-8")
    (d / "trips.txt").write_text(
        "route_id,service_id,trip_id\n"
        "R_S1,S1,S1_trip\nR_BUS,S1,BUS_trip\nR_RE,S1,RE_trip\n",
        encoding="utf-8")
    (d / "calendar.txt").write_text(
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,"
        "sunday,start_date,end_date\n"
        "S1,1,1,1,1,1,1,1,20260101,20261231\n", encoding="utf-8")
    (d / "stop_times.txt").write_text(
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        # S1: H1 23:00 -> H2 23:10
        "S1_trip,23:00:00,23:00:00,H1,1\n"
        "S1_trip,23:10:00,23:10:00,H2,2\n"
        # Bus 421: H2 23:20 -> H3 23:40
        "BUS_trip,23:20:00,23:20:00,H2,1\n"
        "BUS_trip,23:40:00,23:40:00,H3,2\n"
        # RE: H1 23:30 -> H3 23:45 (Direkt)
        "RE_trip,23:30:00,23:30:00,H1,1\n"
        "RE_trip,23:45:00,23:45:00,H3,2\n", encoding="utf-8")
    return str(d)


HOME = (49.4900, 8.5100)
GOAL = (49.4646, 8.2678)


def _src(gtfs_dir):
    return GTFSStaticSource(gtfs_dir, service_date=SERVICE_DATE)


def test_kein_gtfs_klarer_fehler(tmp_path):
    with pytest.raises(GTFSNotAvailableError, match="GTFS-Daten fehlen"):
        GTFSStaticSource(str(tmp_path / "gibtsnicht"))


def test_verbindung_mit_umstieg_und_direkt(gtfs_dir):
    src = _src(gtfs_dir)
    deadline = datetime(2026, 9, 14, 23, 50)
    conns = src.connections(*HOME, *GOAL, deadline)
    assert len(conns) == 2, [c.line_names for c in conns]
    by_lines = {tuple(c.line_names): c for c in conns}
    # Umstieg-Verbindung: S1 + Bus 421, Abfahrt 23:00, Ankunft 23:40
    c1 = by_lines[("S1", "Bus 421")]
    assert c1.changes_count == 1
    assert c1.departure_ts == datetime(2026, 9, 14, 23, 0)
    assert c1.arrival_ts == datetime(2026, 9, 14, 23, 40)
    assert c1.start_halt == "Wohnort Bhf"
    assert c1.dest_halt == "Ellerstadt Ort"
    assert c1.walking_minutes_total > 0    # Fusswege an beiden Enden
    # Direktverbindung: RE 4, Abfahrt 23:30, Ankunft 23:45
    c2 = by_lines[("RE 4",)]
    assert c2.changes_count == 0
    assert c2.arrival_ts == datetime(2026, 9, 14, 23, 45)


def test_ankunft_vor_deadline_filtet(gtfs_dir):
    src = _src(gtfs_dir)
    # Deadline 23:42: nur die Umstieg-Verbindung (23:40) bleibt, RE (23:45) faellt raus
    conns = src.connections(*HOME, *GOAL, datetime(2026, 9, 14, 23, 42))
    assert [c.line_names for c in conns] == [["S1", "Bus 421"]]


def test_keine_verbindung_leerliste(gtfs_dir):
    src = _src(gtfs_dir)
    # Deadline vor allen Ankuenften
    assert src.connections(*HOME, *GOAL,
                           datetime(2026, 9, 14, 22, 0)) == []
    # Ziel ohne Haltestelle im Umkreis (mitten im Nirgendwo, > 1 km)
    far = (48.0, 7.0)
    assert src.connections(*HOME, *far,
                           datetime(2026, 9, 14, 23, 50)) == []


def test_rueckverbindungen(gtfs_dir):
    src = _src(gtfs_dir)
    # Rueckweg Ellerstadt -> Home ab 23:50: BUS 23:40 ist zu frueh;
    # es gibt keine spaetere Rueckfahrt im Soll-Fahrplan -> leer.
    rets = src.return_connections(*GOAL, *HOME, datetime(2026, 9, 14, 23, 50))
    assert rets == []
    # Rueckweg ab 23:35: Bus 421 rueckwaerts? Nicht im Feed (nur H2->H3),
    # Ergebnis bleibt leer - klarer Fall "keine Verbindung", kein Crash.
    assert src.return_connections(*GOAL, *HOME,
                                  datetime(2026, 9, 14, 23, 35)) == []


def test_async_schnittstelle_wie_spec(gtfs_dir):
    src = _src(gtfs_dir)
    conns = asyncio.run(src.fetch_connections(
        HOME[0], HOME[1], GOAL[0], GOAL[1],
        datetime(2026, 9, 14, 23, 50)))
    assert conns and conns[0].line_names == ["S1", "Bus 421"]
    rets = asyncio.run(src.fetch_return_connections(
        HOME[0], HOME[1], GOAL[0], GOAL[1],
        datetime(2026, 9, 14, 23, 50)))
    assert isinstance(rets, list)


def test_determinismus(gtfs_dir):
    src = _src(gtfs_dir)
    deadline = datetime(2026, 9, 14, 23, 50)
    a = src.connections(*HOME, *GOAL, deadline)
    b = src.connections(*HOME, *GOAL, deadline)
    assert a == b


def test_bbox_filter_laedt_nur_region(gtfs_dir):
    # bbox nur ums Wohngebiet: H3 ist keine Start/Ziel-Haltestelle mehr
    # (Trips behalten Aussen-Stops fuer Fahrtdetails) -> keine Verbindung
    src = GTFSStaticSource(gtfs_dir, bbox=(49.40, 8.45, 49.55, 8.60),
                           service_date=SERVICE_DATE)
    ziel_halte = {sid for _d, sid, _n in src.next_stops(*GOAL)}
    assert "H3" not in ziel_halte and ziel_halte == set()
    assert src.connections(*HOME, *GOAL,
                           datetime(2026, 9, 14, 23, 50)) == []


def test_stationen_keine_haltestellen(gtfs_dir):
    src = _src(gtfs_dir)
    # location_type=1 (Station) darf nicht als Halte dienen
    assert "STATION" not in src._stops
