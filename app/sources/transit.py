"""Transit-Source: GTFS-Static-Loader und einfacher Verbindungs-Router.

KEIN API-Client: Fahrplandaten kommen aus einer lokalen GTFS-Static-
Datei (Verzeichnis mit *.txt). Primaerquelle DELFI/gtfs.de (CC 4.0,
docs/SOURCE_LEGAL_REVIEW.md Eintrag 5); ein spaeterer VRN-Static-Feed
kann die Datei ersetzen, ohne die Schnittstelle zu aendern.

V1-Grenzen (bewusst, kein Bug):
- Kein GTFS-Realtime; Sollfahrplan reicht fuer die Planung.
- Router: Direktfahrt + bis zu 2 Umstiege, Umstieg an derselben
  Haltestelle (stop_id) oder per Fussweg <= WALK_TRANSFER_M an den
  Nachbarn-Halt; Fusswege <= 1 km an Start und Ziel
  (walking_minutes_total rechnet alle Fusswege mit).
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
from datetime import date, datetime, time as _dt_time, timedelta
from typing import Optional

GTFS_DIR = os.path.expanduser("~/gtfs")
WALK_RADIUS_M = 1000.0
WALK_TRANSFER_M = 600.0     # Fussweg-Umstieg zwischen Haltestellen
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

    def _walk_neighbors(self, sid) -> list:
        """[(stop_id, gehmin)] fuer Fussweg-Umstieg <= WALK_TRANSFER_M.
        Realer Fall: 625 endet an 'Feudenheim Bstg 2', die RNV 7 Richtung
        Ziel haelt 20 m weiter an 'Bstg 1' - ohne diesen Fussweg-Umstieg
        waere das Netz dort unzugaenglich. Nachbarsuche ueber ein grobes
        Zellgitter (~550m Zellen), damit sie O(1) pro Stop bleibt."""
        if not hasattr(self, "_walk_grid"):
            grid = {}
            for s2, (_n, la, lo) in self._stops.items():
                grid.setdefault((round(la / 0.005), round(lo / 0.007)),
                                []).append(s2)
            self._walk_grid = grid
            self._walk_adj = {}
        if sid in self._walk_adj:
            return self._walk_adj[sid]
        _n, la, lo = self._stops[sid]
        out = []
        clat, clon = round(la / 0.005), round(lo / 0.007)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for s2 in self._walk_grid.get((clat + dy, clon + dx), ()):
                    if s2 == sid:
                        continue
                    _n2, la2, lo2 = self._stops[s2]
                    d = _haversine_m(la, lo, la2, lo2)
                    if d <= WALK_TRANSFER_M:
                        out.append((s2, timedelta(
                            minutes=d / WALK_SPEED_M_PER_MIN)))
        out.sort(key=lambda e: e[1])
        self._walk_adj[sid] = out
        return out

    def _search(self, start_ids: set, goal_ids: set, dep_after: datetime,
                arrive_before: datetime) -> list:
        """Runden-basierte Suche (deterministisch, max. 2 Umstiege):
        Runde 1 = Direktfahrt ab Start, Runde 2/3 = Weiterfahrt ab Hub-
        Stops (>= HUB_MIN_ROUTES Linien), direkt oder per Fussweg-Umstieg.

        Pro Stop werden PARETO-OPTIONEN (frueheste Ankunft, spaeteste
        Erstabfahrt, Pfad) gefuehrt - nicht nur die frueheste Ankunft:
        fuer "letzte Bahn hin" muss beim Umstieg die Anfahrt mit der
        SPAETESTEN Erstabfahrt gewaehlt werden, die den Anschluss noch
        erreicht (die frueheste Ankunft wuerde Morgentrips als Anfahrt
        eines Abend-Anschlusses kombinieren).

        Alle Zeitvergleiche laufen als GTFS-Offsets zum SERVICE-Tag
        (self.service_date), NICHT zum Kalendertag der Abfrage: Fahrten
        nach Mitternacht sind im Feed als 24:xx/25:xx kodiert und
        gehoeren zum Vorabend-Service.
        Rueckgabe: [legs] mit leg = (trip_id, i_start, i_ziel, ankunft_td)."""
        base = datetime.combine(self.service_date, _dt_time.min)
        min_dep = dep_after - base
        max_arr = arrive_before - base
        MT = timedelta(minutes=MIN_TRANSFER_MIN)
        hubs = self._hub_stops()

        results = []   # jede gefundene Verbindung zu einem Ziel-Stop
        seen = set()   # Duplikat-Schutz (identischer Pfad)

        def _goal_candidate(arr, path):
            key = tuple(path)
            if key in seen:
                return
            seen.add(key)
            results.append([(t, i, j, arr if k == len(path) - 1 else None)
                            for k, (t, i, j) in enumerate(path)])

        def _pareto_add(stop_opts, arr, dep_first, path) -> None:
            """Option eintragen, dominierte Eintraege entfernen. Optionen
            sind nach Ankunft aufsteigend sortiert - und damit auch nach
            Erstabfahrt aufsteigend (sonst waeren sie dominiert)."""
            for a2, d2, _p in stop_opts:
                if a2 <= arr and d2 >= dep_first:
                    return                      # dominiert
            stop_opts[:] = [e for e in stop_opts
                            if not (e[0] >= arr and e[1] <= dep_first)]
            stop_opts.append((arr, dep_first, path))
            stop_opts.sort(key=lambda e: e[0])

        def _ride(board: dict) -> dict:
            """board: stop -> [(ankunft, erste_abfahrt, pfad)] (Pareto).
            Eine Runde weiterfahren: pro Boarding die Option mit der
            spaetesten Erstabfahrt nehmen, die den Umstieg noch schafft."""
            out = {}
            for tid in sorted(self._trip_times):
                seq = self._trip_times[tid]
                for i, (sid, _sq, dep, _sa) in enumerate(seq):
                    opts = board.get(sid)
                    if not opts:
                        continue
                    best = None
                    for a, d, p in opts:
                        if a + MT <= dep:
                            best = (d, p)       # letzte passende Option
                    if best is None:
                        continue
                    d, p = best
                    for j in range(i + 1, len(seq)):
                        sid2, _s2, _d2, arr2 = seq[j]
                        if arr2 > max_arr:
                            break
                        np = p + [(tid, i, j)]
                        if sid2 in goal_ids:
                            _goal_candidate(arr2, np)
                        _pareto_add(out.setdefault(sid2, []), arr2, d, np)
            return out

        def _board_set(reach: dict) -> dict:
            """Boarding-Optionen nach einer Runde: direkt am erreichten
            Hub-Stop plus Fussweg-Umstieg (<= WALK_TRANSFER_M) zu
            Nachbar-Hubs (Optionen um die Gehzeit verschoben)."""
            board = {}
            for s, opts in reach.items():
                for a, d, p in opts:
                    if s in hubs and s not in start_ids:
                        _pareto_add(board.setdefault(s, []), a, d, p)
                    for s2, walk in self._walk_neighbors(s):
                        if s2 not in hubs or s2 in start_ids:
                            continue
                        _pareto_add(board.setdefault(s2, []),
                                    a + walk, d, p)
            return board

        # Runde 1: Direktfahrten ab Start - jede Abfahrt eine eigene Option
        r1 = {}
        for tid in sorted(self._trip_times):
            seq = self._trip_times[tid]
            for i, (sid, _sq, dep, _sa) in enumerate(seq):
                if sid not in start_ids or dep < min_dep:
                    continue
                for j in range(i + 1, len(seq)):
                    sid2, _s2, _d2, arr2 = seq[j]
                    if arr2 > max_arr:
                        break
                    path = [(tid, i, j)]
                    if sid2 in goal_ids:
                        _goal_candidate(arr2, path)
                    _pareto_add(r1.setdefault(sid2, []), arr2, dep, path)

        # Runde 2 (1 Umstieg) und Runde 3 (2 Umstiege)
        r2 = _ride(_board_set(r1))
        _ride(_board_set(r2))

        print(f"[GTFS] suche service_day={self.service_date} "
              f"min_dep={min_dep} max_arr={max_arr}: "
              f"{len(results)} verbindungen (r1_stops={len(r1)}, "
              f"r2_stops={len(r2)})", flush=True)
        return results

    def _connection(self, legs, base: datetime, walk_start_min: float,
                    walk_dest_min: float) -> Connection:
        seq0 = self._trip_times[legs[0][0]]
        seq_last = self._trip_times[legs[-1][0]]
        start_sid = seq0[legs[0][1]][0]
        dest_sid = seq_last[legs[-1][2]][0]
        # Fusswege zwischen Umstieg-Stops (leg-Ende != naechster leg-Start)
        walk_transfer_min = 0.0
        for k in range(len(legs) - 1):
            s_a = self._trip_times[legs[k][0]][legs[k][2]][0]
            s_b = self._trip_times[legs[k + 1][0]][legs[k + 1][1]][0]
            if s_a != s_b:
                _n1, la1, lo1 = self._stops[s_a]
                _n2, la2, lo2 = self._stops[s_b]
                walk_transfer_min += _haversine_m(
                    la1, lo1, la2, lo2) / WALK_SPEED_M_PER_MIN
        return Connection(
            start_halt=self._stops.get(start_sid, ("?",))[0],
            dest_halt=self._stops.get(dest_sid, ("?",))[0],
            departure_ts=base + seq0[legs[0][1]][2],
            arrival_ts=base + legs[-1][3],
            changes_count=len(legs) - 1,
            line_names=[self._line(t) for t, *_ in legs],
            walking_minutes_total=round(walk_start_min + walk_dest_min
                                        + walk_transfer_min, 1))

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
        """Verbindungen, die VOR arrival_before ankommen. Fenster: bis zu
        12 Stunden (Spaetfenster wie 02:00 brauchen die Abendbusse).
        Alle Offsets auf den Service-Tag (siehe _search)."""
        starts = self.next_stops(start_lat, start_lon)
        goals = self.next_stops(dest_lat, dest_lon)
        if not starts or not goals:
            return []
        base = datetime.combine(self.service_date, _dt_time.min)
        dep_from = max(arrival_before - timedelta(hours=12), base)
        print(f"[GTFS] hin {starts[0][2]} -> {goals[0][2]}: "
              f"ankunft_bis={arrival_before} service_base={base}",
              flush=True)
        legs_list = self._search(
            {s[1] for s in starts}, {g[1] for g in goals},
            dep_from, arrival_before)
        out = []
        seen_conns = set()
        for legs in legs_list:
            c = self._connection(legs, base,
                                 starts[0][0] / WALK_SPEED_M_PER_MIN,
                                 goals[0][0] / WALK_SPEED_M_PER_MIN)
            if c.arrival_ts > arrival_before:
                continue
            # Plausibilitaet: 11-Stunden-Um-die-Häuser-Touren (fruehe
            # Abfahrt + stundenlange Regional-Legs) sind keine Option.
            if c.arrival_ts - c.departure_ts > timedelta(hours=3):
                continue
            key = (c.departure_ts, c.arrival_ts, tuple(c.line_names),
                   c.dest_halt)
            if key in seen_conns:
                continue
            seen_conns.add(key)
            out.append(c)
        # Späteste Abfahrt zuerst: der Deployment-Fall will die LETZTE
        # Bahn, die noch rechtzeitig ankommt (fruehe zuerst wuerde das
        # [:-Slicing] genau die letzte verlieren).
        out.sort(key=lambda c: (c.departure_ts, c.arrival_ts,
                                c.changes_count), reverse=True)
        return out[:20]

    def return_connections(self, from_lat, from_lon, home_lat, home_lon,
                           departure_after) -> list:
        """Frueehste Rueckverbindungen ab departure_after. Base ist der
        SERVICE-Tag: eine Rueckfahrt um 01:00 (nach Mitternacht) ist im
        Feed als 25:00 des Vorabend-Services kodiert - genau so wird sie
        hier gesucht, statt stumpf 01:00 gegen den falschen Tag zu joinen."""
        starts = self.next_stops(from_lat, from_lon)
        goals = self.next_stops(home_lat, home_lon)
        if not starts or not goals:
            return []
        base = datetime.combine(self.service_date, _dt_time.min)
        print(f"[GTFS] rueck {starts[0][2]} -> {goals[0][2]}: "
              f"abfahrt_ab={departure_after} service_base={base} "
              f"min_dep_offset={departure_after - base}", flush=True)
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
