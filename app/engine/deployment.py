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
    # Dynamic Abort: Wetterumschlag im laufenden Fenster
    # {"time": "23:00", "reason": "..."} oder None
    dynamic_abort: Optional[dict] = None



def resolve_window_times(gw, now, horizon_hours: int = 2):
    """Zeit-Logik der Transit-Planung (Fix 30.09.: keine historischen
    Verbindungen mehr).

    Liefert (gws, gwe, from_now):
    - kein GW ueberliefert              -> Route ab JETZT (now..now+H)
    - GW komplett vorbei (end <= now)   -> Route ab JETZT (Zeit-Parameter
      ignorieren - sonst kaeme "Abfahrt 17:22" bei 22:48 heraus)
    - GW laeuft/lief kuerzlich an       -> (start evtl. auf now) .. end
    """
    if not gw:
        return now, now + timedelta(hours=horizon_hours), True
    night = datetime.fromisoformat(gw["night"])
    gws = datetime.combine(night,
                           datetime.strptime(gw["start"], "%H:%M").time())
    gwe = gws + timedelta(hours=max(1, gw.get("hours", 1)))
    if gwe <= now:
        # Fenster liegt komplett zurueck: naechste Verbindungen ab jetzt
        return now, now + timedelta(hours=horizon_hours), True
    if gws < now:
        # Fenster laeuft: Planung ab jetzt, Ankunft bis Fensterende ok
        return now, gwe, False
    return gws, gwe, False


def deployment_window(golden_window_start: datetime,
                      golden_window_end: datetime,
                      home_location: dict,
                      obs_location: dict,
                      transit_source: GTFSStaticSource,
                      setup_minutes: int = 30,
                      abort_at: datetime | None = None) -> DeploymentPlan:
    """Berechnet Hin- und Rueckfahrtfenster fuer ein Golden Window.

    - latest_departure: letzte Verbindung ab home, die VOR
      golden_window_start + setup_buffer am Ziel ankommt
    - extraction: frueheste Rueckverbindung NACH golden_window_end
    - extraction_warning_ts: 30 min vor extraction_departure
      ("Zeit zum Abbauen")
    - abort_at: Dynamic-Abort-Zeitpunkt (Wetterumschlag im laufenden
      Fenster) - verlegt die Rueckfahrt-Suche VOR den Umschlag
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
            "steps": best.steps,
        }
    else:
        plan.latest_departure = None  # keine Bahn vor Fenster - klar signalisieren

    # Rueckfahrt: normal ab Fensterende - aber bei Dynamic Abort
    # (Wetterumschlag im laufenden Fenster) ab dem Umschlagpunkt.
    return_after = golden_window_end
    if abort_at is not None and abort_at < return_after:
        return_after = abort_at
        plan.dynamic_abort = {
            "time": abort_at.strftime("%H:%M"),
        }
        print(f"[DEPLOY] DYNAMIC ABORT: Rueckfahrt-Suche auf "
              f"{abort_at:%H:%M} vorverlegt (Wetterumschlag)", flush=True)
    rets = transit_source.return_connections(
        obs_location["lat"], obs_location["lon"],
        home_location["lat"], home_location["lon"],
        return_after)
    if rets:
        r = rets[0]  # frueheste (sortiert)
        plan.extraction = {
            "earliest_return": r.departure_ts.strftime("%H:%M"),
            "changes": r.changes_count,
            "lines": r.line_names,
            "arrival_home": r.arrival_ts.strftime("%H:%M"),
            "steps": r.steps,
        }
        warn = r.departure_ts - timedelta(minutes=30)
        plan.extraction_warning_ts = warn.strftime("%H:%M")
    return plan
