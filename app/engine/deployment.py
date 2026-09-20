"""Deployment-Logik: Wann muss die letzte Bahn losfahren, um rechtzeitig
am Standort zu sein - und wann faehrt die erste Bahn zurueck?

Nutzt GTFSStaticSource (VRN-Static als Primaerquelle, DELFI als
dokumentierter Fallback - siehe docs/SOURCE_LEGAL_REVIEW.md 4+5).
Reine Planungslogik, keine Empfehlungen."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from ..sources.transit import GTFSStaticSource, GTFSNotAvailableError


@dataclass
class DeploymentPlan:
    destination: str
    golden_window_start: Optional[str] = None
    golden_window_end: Optional[str] = None
    latest_departure: Optional[dict] = None
    extraction: Optional[dict] = None
    extraction_warning_ts: Optional[str] = None
    setup_buffer_min: int = 30
    transit_source: str = "vrn_gtfs"
    error: Optional[str] = None


def deployment_window(golden_window_start: datetime,
                      golden_window_end: datetime,
                      home_location: dict,
                      obs_location: dict,
                      transit_source: GTFSStaticSource,
                      setup_minutes: int = 30) -> DeploymentPlan:
    """Berechnet Hin- und Rueckfahrtfenster fuer ein Golden Window.

    - latest_departure: letzte Verbindung ab home, die VOR
      golden_window_start + setup_buffer am Ziel ankommt
    - extraction: frueheste Rueckverbindung NACH golden_window_end
    - extraction_warning_ts: 30 min vor extraction_departure
      ("Zeit zum Abbauen")
    """
    plan = DeploymentPlan(
        destination=obs_location.get("name", "?"),
        golden_window_start=golden_window_start.strftime("%H:%M"),
        golden_window_end=golden_window_end.strftime("%H:%M"),
        setup_buffer_min=setup_minutes)

    arrive_by = golden_window_start - timedelta(minutes=setup_minutes)
    if golden_window_start <= datetime.now():
        # Fenster laeuft bereits: Der Setup-Puffer ist nicht mehr
        # einhaltbar - stattdessen jede Fahrt erlauben, die noch
        # WAEHREND des Fensters ankommt (kein stumpfes Leeres-Ergebnis).
        arrive_by = golden_window_end
        print(f"[DEPLOY] Fenster laeuft bereits: Ankunft-Deadline "
              f"auf Fensterende {arrive_by:%H:%M} erweitert", flush=True)
    conns = transit_source.connections(
        home_location["lat"], home_location["lon"],
        obs_location["lat"], obs_location["lon"], arrive_by)
    if conns:
        best = max(conns, key=lambda c: c.departure_ts)
        plan.latest_departure = {
            "time": best.departure_ts.strftime("%H:%M"),
            "from": best.start_halt, "to": best.dest_halt,
            "changes": best.changes_count,
            "lines": best.line_names,
            "walking_min": best.walking_minutes_total,
            "arrival": best.arrival_ts.strftime("%H:%M"),
        }
    else:
        plan.latest_departure = None  # keine Bahn vor Fenster - klar signalisieren

    rets = transit_source.return_connections(
        obs_location["lat"], obs_location["lon"],
        home_location["lat"], home_location["lon"],
        golden_window_end)
    if rets:
        r = rets[0]  # frueheste (sortiert)
        plan.extraction = {
            "earliest_return": r.departure_ts.strftime("%H:%M"),
            "changes": r.changes_count,
            "lines": r.line_names,
            "arrival_home": r.arrival_ts.strftime("%H:%M"),
        }
        warn = r.departure_ts - timedelta(minutes=30)
        plan.extraction_warning_ts = warn.strftime("%H:%M")
    return plan
