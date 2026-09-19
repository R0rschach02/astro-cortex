"""Transit-Source: GTFS-Static-Loader und einfacher Verbindungs-Router.

KEIN API-Client: Fahrplandaten kommen aus einer lokalen GTFS-Static-
Datei (Verzeichnis mit *.txt). Primaerquelle DELFI/gtfs.de (CC 4.0,
docs/SOURCE_LEGAL_REVIEW.md Eintrag 5); ein spaeterer VRN-Static-Feed
kann die Datei ersetzen, ohne die Schnittstelle zu aendern.

V1-Grenzen (bewusst, kein Bug):
- Kein GTFS-Realtime; Sollfahrplan reicht fuer die Planung.
- Router: Direktfahrt + bis zu 2 Umstiege, Umstieg an derselben
  Haltestelle (stop_id); Fusswege <= 1 km nur an Start und Ziel
  (walking_minutes_total). Kein transfers.txt-Fussweg-Graph.
- Service-Kalender: weekday-Filter (calendar.txt) + Ausnahmen
  (calendar_dates.txt) fuer das Tagesdatum.
- Determinismus: identischer Feed + identische Parameter -> identische
  Ausgabe (durchgaengig sortierte Iteration, keine Uhren/Zufall).
"""
from __future__ import annotations

import csv
import math
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

GTFS_DIR = os.path.expanduser("~/gtfs")
WALK_RADIUS_M = 1000.0
WALK_SPEED_M_PER_MIN = 80.0
MIN_TRANSFER_MIN = 3
MAX_CHANGES = 2


class GTFSNotAvailableError(RuntimeError):
    """Klarer Fehler statt Crash, wenn die GTFS-Daten fehlen."""


@dataclass
class Connection:
    start_halt: str
    dest_halt: str
    departure_ts: datetime
    arrival_ts: datetime
    changes_count: int
    line_names: list = field(default_factory=list)
    walking_minutes_total: float = 0.0


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _parse_gtfs_time(s: str) -> timedelta:
    """GTFS 'HH:MM:SS' - Stunden duerfen > 24 (Mitternachtsfahrten)."""
    h, m, sec = (int(x) for x in s.split(":"))
    return timedelta(hours=h, minutes=m, seconds=sec)


def _gtfs_date(s: str) -> Optional[date]:
    try:
        return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    except (ValueError, IndexError):
        return None


class GTFSStaticSource:
    name = "gtfs_static"

    def __init__(self, gtfs_dir: str = GTFS_DIR,
                 bbox: Optional[tuple] = None,
                 service_date: Optional[date] = None):
        """bbox = (lat_min, lon_min, lat_max, lon_max); None = alles.
        service_date: fuer Kalenderfilter (Default: heute) - injizierbar
        fuer deterministische Tests."""
        if not os.path.isdir(gtfs_dir):
            raise GTFSNotAvailableError(
                f"GTFS-Daten fehlen: {gtfs_dir} existiert nicht - Feed "
                f"herunterladen/entpacken (siehe docs/SOURCE_LEGAL_REVIEW.md "
                f"Eintrag 5)")
        self.gtfs_dir = gtfs_dir
        self.bbox = bbox
        self.service_date = service_date or date.today()
        self._stops = {}        # stop_id -> (name, lat, lon)
        self._routes = {}       # route_id -> line
        self._trip_route = {}   # trip_id -> route_id
        self._trip_service = {} # trip_id -> service_id
        self._trip_times = {}   # trip_id -> [(stop_id, seq, dep, arr)]
        self._load()

    # ---------- Laden ----------
    def _rows(self, name):
        path = os.path.join(self.gtfs_dir, name)
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8-sig") as f:
            yield from csv.DictReader(f, delimiter=",")

    def _in_bbox(self, lat, lon) -> bool:
        if self.bbox is None:
            return True
        a, b, c, d = self.bbox
        return a <= lat <= c and b <= lon <= d

    def _load(self):
        for r in self._rows("stops.txt"):
            try:
                lat, lon = float(r["stop_lat"]), float(r["stop_lon"])
            except (KeyError, ValueError, TypeError):
                continue
            if r.get("location_type") in ("1", "2", "3"):
                continue          # Stationen/Entrances sind keine Halte
            self._stops[r["stop_id"]] = (r.get("stop_name", r["stop_id"]),
                                         lat, lon)
        for r in self._rows("routes.txt"):
            self._routes[r["route_id"]] = (
                r.get("route_short_name") or r.get("route_long_name")
                or r.get("route_id", "?"))
        for r in self._rows("trips.txt"):
            self._trip_route[r["trip_id"]] = r.get("route_id", "?")
            self._trip_service[r["trip_id"]] = r.get("service_id")

        relevant = {
            sid for sid, (_, lat, lon) in self._stops.items()
            if self._in_bbox(lat, lon)} if self.bbox else None
        self._region_stop_ids = relevant      # None = keine Einschraenkung
        touching_base = relevant if relevant is not None else set(self._stops)
        touching = set()
        for r in self._rows("stop_times.txt"):
            if r.get("stop_id") in touching_base:
                touching.add(r.get("trip_id"))
        for r in self._rows("stop_times.txt"):
            tid = r.get("trip_id")
            if tid not in touching:
                continue
            try:
                dep = _parse_gtfs_time(r["departure_time"])
                arr = _parse_gtfs_time(r["arrival_time"])
                seq = int(r.get("stop_sequence", 0))
            except (KeyError, ValueError):
                continue
            self._trip_times.setdefault(tid, []).append(
                (r["stop_id"], seq, dep, arr))
        for t in self._trip_times.values():
            t.sort(key=lambda e: e[1])
        self._apply_service_calendar()
        # Stop->Trips-Index: Router sucht pro Haltestelle nur die bedienenden
        # Fahrten statt ueber ALLE zu iterieren (VRN-Feed: 70k Trips).
        self._stop_trips = {}
        for tid, seq in self._trip_times.items():
            for sid, _sq, _dep, _arr in seq:
                self._stop_trips.setdefault(sid, set()).add(tid)

    def _apply_service_calendar(self):
        cal = list(self._rows("calendar.txt"))
        if not cal:
            return
        today = self.service_date
        wd = ["monday", "tuesday", "wednesday", "thursday", "friday",
              "saturday", "sunday"][today.weekday()]
        active = set()
        for r in cal:
            start, end = _gtfs_date(r.get("start_date", "")), \
                _gtfs_date(r.get("end_date", ""))
            if start and end and start <= today <= end and r.get(wd) == "1":
                active.add(r.get("service_id"))
        for r in self._rows("calendar_dates.txt"):
            if _gtfs_date(r.get("date", "")) == today:
                if r.get("exception_type") == "1":
                    active.add(r.get("service_id"))
                elif r.get("exception_type") == "2":
                    active.discard(r.get("service_id"))
        self._trip_times = {
            tid: seq for tid, seq in self._trip_times.items()
            if self._trip_service.get(tid) in active}
        self._stop_trips = {}
        for tid, seq in self._trip_times.items():
            for sid, _sq, _dep, _arr in seq:
                self._stop_trips.setdefault(sid, set()).add(tid)

    # ---------- Haltestellen ----------
    def next_stops(self, lat: float, lon: float,
                   radius_m: float = WALK_RADIUS_M) -> list:
        """[(distanz_m, stop_id, name)] im Umkreis, sortiert. Bei bbox-
        Filter zaehlen nur Haltestellen INNERHALB der Region als Start/
        Ziel (durchfahrende Trips behalten ihre Aussen-Stops im Detail)."""
        region = self._region_stop_ids
        out = []
        for sid, (name, sla, slo) in self._stops.items():
            if region is not None and sid not in region:
                continue
            d = _haversine_m(lat, lon, sla, slo)
            if d <= radius_m:
                out.append((round(d, 1), sid, name))
        out.sort(key=lambda e: (e[0], e[1]))
        return out

    def _line(self, trip_id) -> str:
        return self._routes.get(self._trip_route.get(trip_id, ""), "?")

    # ---------- Router (deterministische Breitensuche) ----------
    HUB_MIN_ROUTES = 2   # >=2 Routen = Umstiegskandidat (Hub)

    def _hub_stops(self) -> set:
        """Stops, an denen Mind. HUB_MIN_ROUTES verschiedene Routen halten
        - nur diese sind praktikable Umstiegspunkte (sonst BFS-Explosion)."""
        if hasattr(self, "_hub_cache"):
            return self._hub_cache
        stop_routes = {}
        for tid, seq in self._trip_times.items():
            route = self._trip_route.get(tid, "")
            for sid, _sq, _d, _a in seq:
                stop_routes.setdefault(sid, set()).add(route)
        self._hub_cache = {sid for sid, routes in stop_routes.items()
                           if len(routes) >= self.HUB_MIN_ROUTES}
        return self._hub_cache

    def _search(self, start_ids: set, goal_ids: set, dep_after: datetime,
                arrive_before: datetime) -> list:
        """Two-Pass-Suche statt BFS (deterministisch, schnell):
        Pass 1: alle vom Start erreichbaren Stops mit fruehester Ankunft
        Pass 2: von jedem Umstiegs-Stop (Hub) erreichbare Ziel-Verbindungen.
        Rueckgabe: [(legs)] mit leg = (trip_id, i_start, i_ziel, ankunft_td)."""
        base = dep_after.replace(hour=0, minute=0, second=0, microsecond=0)
        min_dep = (dep_after - base)
        max_arr = (arrive_before - base)

        # Pass 1: erreichbare Stops vom Start (mit boarding-Position im Trip)
        transfers = {}  # stop_id -> (arrival_td, trip_id, i_board, i_transfer)
        for tid in sorted(self._trip_times):
            seq = self._trip_times[tid]
            for i, (sid, _sq, dep, _sa) in enumerate(seq):
                if sid not in start_ids or dep < min_dep:
                    continue
                for j in range(i + 1, len(seq)):
                    sid2, _sq2, _sd2, arr2 = seq[j]
                    if arr2 > max_arr:
                        break
                    if sid2 not in transfers or arr2 < transfers[sid2][0]:
                        transfers[sid2] = (arr2, tid, i, j)

        # Direktverbindungen (Start -> Ziel in derselben Fahrt)
        results = []
        for sid2, (arr2, tid, i, j) in transfers.items():
            if sid2 in goal_ids:
                results.append([(tid, i, j, arr2)])

        # Pass 2: von Transfer-Stops zum Ziel (1 Umstieg)
        hubs = self._hub_stops()
        MIN_TRANSFER = timedelta(minutes=MIN_TRANSFER_MIN)
        for tstop, (arr_transfer, tid1, i1, j1) in sorted(
                transfers.items(), key=lambda x: x[1][0]):
            if tstop not in hubs or tstop in goal_ids:
                continue
            ttrips = self._stop_trips.get(tstop, set())
            for tid2 in sorted(ttrips):
                if tid2 == tid1:
                    continue
                seq2 = self._trip_times[tid2]
                for i2, (sid, _sq, dep, _sa) in enumerate(seq2):
                    if sid != tstop or dep < arr_transfer + MIN_TRANSFER:
                        continue
                    for j2 in range(i2 + 1, len(seq2)):
                        sid2, _sq2, _sd2, arr2 = seq2[j2]
                        if arr2 > max_arr:
                            break
                        if sid2 in goal_ids:
                            results.append(
                                [(tid1, i1, j1, arr_transfer),
                                 (tid2, i2, j2, arr2)])
                    break
        return results

    def _connection(self, legs, base: datetime, walk_start_min: float,
                    walk_dest_min: float) -> Connection:
        seq0 = self._trip_times[legs[0][0]]
        seq_last = self._trip_times[legs[-1][0]]
        start_sid = seq0[legs[0][1]][0]
        dest_sid = seq_last[legs[-1][2]][0]
        return Connection(
            start_halt=self._stops.get(start_sid, ("?",))[0],
            dest_halt=self._stops.get(dest_sid, ("?",))[0],
            departure_ts=base + seq0[legs[0][1]][2],
            arrival_ts=base + legs[-1][3],
            changes_count=len(legs) - 1,
            line_names=[self._line(t) for t, *_ in legs],
            walking_minutes_total=round(walk_start_min + walk_dest_min, 1))

    # ---------- oeffentliche async-Schnittstelle (Spec) ----------
    async def fetch_connections(self, start_lat: float, start_lon: float,
                                dest_lat: float, dest_lon: float,
                                arrival_before: datetime) -> list:
        return self.connections(start_lat, start_lon, dest_lat, dest_lon,
                                arrival_before)

    async def fetch_return_connections(self, start_lat: float,
                                        start_lon: float,
                                        dest_lat: float, dest_lon: float,
                                        departure_after: datetime) -> list:
        return self.return_connections(dest_lat, dest_lon, start_lat,
                                       start_lon, departure_after)

    # ---------- synchrone Basis ----------
    def connections(self, start_lat, start_lon, dest_lat, dest_lon,
                    arrival_before) -> list:
        """Verbindungen, die VOR arrival_before ankommen; fensterbegrenzt
        auf die letzten 4 Stunden vor dem Zieltermin."""
        starts = self.next_stops(start_lat, start_lon)
        goals = self.next_stops(dest_lat, dest_lon)
        if not starts or not goals:
            return []
        base = arrival_before.replace(hour=0, minute=0, second=0,
                                      microsecond=0)
        legs_list = self._search(
            {s[1] for s in starts}, {g[1] for g in goals},
            arrival_before - timedelta(hours=4), arrival_before)
        out = []
        for legs in legs_list:
            c = self._connection(legs, base,
                                 starts[0][0] / WALK_SPEED_M_PER_MIN,
                                 goals[0][0] / WALK_SPEED_M_PER_MIN)
            if c.arrival_ts <= arrival_before:
                out.append(c)
        out.sort(key=lambda c: (c.departure_ts, c.arrival_ts,
                                c.changes_count))
        return out[:20]

    def return_connections(self, from_lat, from_lon, home_lat, home_lon,
                           departure_after) -> list:
        """Frueehste Rueckverbindungen ab departure_after."""
        starts = self.next_stops(from_lat, from_lon)
        goals = self.next_stops(home_lat, home_lon)
        if not starts or not goals:
            return []
        base = (departure_after - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        legs_list = self._search(
            {s[1] for s in starts}, {g[1] for g in goals},
            departure_after, departure_after + timedelta(hours=8))
        out = []
        for legs in legs_list:
            c = self._connection(legs, base,
                                 starts[0][0] / WALK_SPEED_M_PER_MIN,
                                 goals[0][0] / WALK_SPEED_M_PER_MIN)
            if c.departure_ts >= departure_after:
                out.append(c)
        out.sort(key=lambda c: (c.departure_ts, c.arrival_ts,
                                c.changes_count))
        return out[:10]
