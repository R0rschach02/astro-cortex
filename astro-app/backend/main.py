#!/usr/bin/env python3
"""

# Zeitzone VOR allen datetime-Nutzungen erzwingen (Fix 30.09.: Timer-
# Dienste liefen auf UTC - Logs 21:16 vs. Realitaet 23:16, und die
# Transit-Engine mischte UTC-NOW mit CEST-Golden-Windows, was die
# Fenster-Logik um 2h verschob).
import os as _os, time as _time
_os.environ["TZ"] = "Europe/Berlin"
_time.tzset()
Astro Command Center - FastAPI-Backend.

Liest ausschliesslich aus, was der Crawler (astro_crawler.py, systemd-Timer)
schreibt: SQLite-Historie, State-Datei, Watchlist, Mond-Cache. Einzige
Schreibstelle: POST /api/watch (mit dem gemeinsamen fcntl-Watchlist-Lock).

Laeuft als systemd-User-Dienst (astro-app.service) auf 127.0.0.1:8000.
Feld-Zugriff spaeter via Tailscale (dann --host an die Tailnet-IP und
'tailscale serve' fuer HTTPS - noetig fuer Geolocation in der PWA).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from typing import Optional

# astro_crawler liegt im Home-Verzeichnis (~), nicht im Site-Packages
sys.path.insert(0, os.path.expanduser("~"))

import astro_crawler as ac  # noqa: E402

from fastapi import FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import (FileResponse, HTMLResponse,
                     Response)  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("astro-app")

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
# Optionaler Schutz schreibender Endpunkte: env ASTRO_API_TOKEN setzen
API_TOKEN = os.environ.get("ASTRO_API_TOKEN", "")

app = FastAPI(title="Astro Command Center", version="1.0")


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _db():
    conn = sqlite3.connect(ac.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _age_minutes(ts_iso: str) -> Optional[int]:
    try:
        ts = dt.datetime.fromisoformat(ts_iso)
        return int((dt.datetime.now() - ts).total_seconds() / 60)
    except (ValueError, TypeError):
        return None


def _spot_state(loc: dict, profile: str = "dso") -> dict:
    """Kombinierte Sicht pro Standort:
    - Seeing/Wolken/Rating aus dem juengsten HEAVY-Crawl (30-Min-Takt)
    - Radar/Wind/Taupunkt + Nachtverlauf aus dem juengsten Lauf (5-Min-Takt)
    - Mond/Dunkelheit/Planeten aus dem Tages-Cache (skyfield)
    Rating wird mit dem aktiven Beobachtungsprofil (dso|planet) berechnet.
    """
    out = {"name": loc["name"], "id": loc.get("id", ""),
           "lat": loc["lat"], "lon": loc["lon"],
           "is_live": loc["name"].startswith("Live "), "age_min": None,
           "bortle_class": loc.get("bortle_class"),
           "elevation_m": loc.get("elevation_m", 100)}
    conn = _db()
    try:
        heavy = conn.execute(
            "SELECT * FROM crawls WHERE location_name = ? AND mode = 'heavy' "
            "ORDER BY id DESC LIMIT 1", (loc["name"],)).fetchone()
        latest = conn.execute(
            "SELECT * FROM crawls WHERE location_name = ? "
            "ORDER BY id DESC LIMIT 1", (loc["name"],)).fetchone()
    finally:
        conn.close()
    src = heavy or latest
    if src:
        out.update({
            "ts": src["ts"], "age_min": _age_minutes(src["ts"]),
            "clouds_total": src["clouds_total"],
            "clouds_lmh": [src["clouds_low"], src["clouds_mid"], src["clouds_high"]],
            "clouds_source": src["clouds_source"],
            "rain_prob": src["rain_prob"],
            "seeing": src["seeing"], "seeing_index": src["seeing_index"],
            "jetstream": src["jetstream"],
        })
    if latest:
        out.update({
            "radar_status": latest["radar_status"],
            "precip_2h": latest["precip_2h"],
            "wind_speed": latest["wind_speed"],
            "dewpoint_spread": latest["dewpoint_spread"],
            "night_temp_min": latest["night_temp_min"],
            "night_temp_max": latest["night_temp_max"],
            "night_rh_max": latest["night_rh_max"],
            "wind_gusts": latest["wind_gusts"],
            "dew_risk": latest["dew_risk"],
            "radar_age_min": _age_minutes(latest["ts"]),
        })
    m = ac.moon_cached(loc["lat"], loc["lon"])
    if m:
        out["moon"] = m
        out["dark_window"] = m.get("dark")
        out["planets"] = m.get("planets")
    # Luecke 1: Inversions-Adjustierung fuer hochgelegene Standorte
    inv = _inversion_adjusted(loc, out.get("clouds_total"),
                              out.get("dewpoint_spread"),
                              out.get("wind_speed"))
    out["inversion"] = inv
    if inv["inversion_likely"]:
        out["clouds_total_adjusted"] = inv["clouds_adjusted"]

    # Rating live mit dem aktiven Profil (nicht den DB-Wert nachspielen).
    # radar_status darf nie None sein (rate() erwartet einen String)
    rep = ac.SiteReport(name=loc["name"], lat=loc["lat"], lon=loc["lon"])
    rep.radar_status = "Unknown"
    for f in ("clouds_total", "seeing", "jetstream", "radar_status",
              "moon_illum", "dew_risk", "planets"):
        if out.get(f) is not None:
            setattr(rep, f, out[f])
    out["rating"], _icon = rep.rate(profile)
    return out


# ---------------------------------------------------------------------------
# API-Endpunkte
# ---------------------------------------------------------------------------

@app.get("/api/spots")
def api_spots():
    """Aktueller Stand aller festen Spots + aktiver Watchlist-Eintraege.
    Ratings nach dem globalen Beobachtungsprofil (dso|planet)."""
    profile = ac.get_profile(ac.load_state())
    spots = [_spot_state(loc, profile)
             for loc in ac.active_locations(ac.DEFAULT_LOCATIONS)]
    return {"ts": dt.datetime.now().isoformat(timespec="seconds"),
            "profile": profile, "spots": spots}


class ProfileBody(BaseModel):
    profile: str


@app.post("/api/profile")
def api_profile(body: ProfileBody, request: Request):
    """Beobachtungsprofil schalten (Pendant zum Bot-Befehl /mode)."""
    if API_TOKEN and request.headers.get("x-api-token") != API_TOKEN:
        raise HTTPException(401, "Ungueltiger API-Token")
    if body.profile not in ("dso", "planet"):
        raise HTTPException(400, "Profil muss 'dso' oder 'planet' sein")
    ac.set_profile(body.profile)
    return {"ok": True, "profile": body.profile}


@app.get("/api/history")
def api_history(location: str, hours: int = Query(24, ge=1, le=336)):
    """Zeitreihe pro Standort (Standard: letzte 24 h) - Basis fuer Graphen."""
    since = (dt.datetime.now() - dt.timedelta(hours=hours)
             ).isoformat(timespec="seconds")
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT ts, mode, clouds_total, clouds_low, clouds_mid, clouds_high, "
            "rain_prob, seeing, jetstream, seeing_index, radar_status, "
            "precip_2h, wind_speed, dewpoint_spread, moon_illum, rating "
            "FROM crawls WHERE location_name = ? AND ts >= ? ORDER BY ts",
            (location, since)).fetchall()
    finally:
        conn.close()
    return {"location": location, "hours": hours,
            "rows": [dict(r) for r in rows]}


@app.get("/api/moon")
def api_moon(lat: float, lon: float):
    """Mond-Daten fuer beliebige Koordinaten (lokal via skyfield, gecacht)."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(400, "Koordinaten ausserhalb des Bereichs")
    m = ac.moon_cached(lat, lon)
    if not m:
        raise HTTPException(503, "Mond-Berechnung fehlgeschlagen")
    return m


class WatchBody(BaseModel):
    lat: float
    lon: float
    hours: float = 2.0
    name: Optional[str] = None


@app.post("/api/watch")
async def api_watch(request: Request):
    """Live-Standort auf die Watchlist (Pendant zu /watch per Telegram).
    Nutzt denselben fcntl-Lock wie der Bot - keine lost updates.
    Cloudflare-Access blockt CORS-Preflights (403, nie mit Cookie) -
    der native Ping kommt deshalb als SIMPLE REQUEST: Content-Type
    text/plain ohne Custom-Header (Body bleibt JSON), Token optional
    per Query-Parameter. Das JSON-Parsing passiert hier manuell."""
    if API_TOKEN and request.headers.get("x-api-token") != API_TOKEN             and request.query_params.get("token") != API_TOKEN:
        raise HTTPException(401, "Ungueltiger API-Token")
    try:
        body = WatchBody(**(await request.json()))
    except Exception as e:
        raise HTTPException(400, f"Ungueltiger Body: {type(e).__name__}")
    if not (-90 <= body.lat <= 90 and -180 <= body.lon <= 180):
        raise HTTPException(400, "Koordinaten ausserhalb des Bereichs")
    name = body.name or f"Live {body.lat:.4f}/{body.lon:.4f}"
    expires = (dt.datetime.now() + dt.timedelta(hours=body.hours)).isoformat()
    with ac.watchlist_lock():
        entries = [e for e in ac.load_watchlist()
                   if abs(e["lat"] - body.lat) > 0.01
                   or abs(e["lon"] - body.lon) > 0.01]
        entries.append({"name": name, "lat": body.lat, "lon": body.lon,
                        "expires": expires})
        ac.save_watchlist(entries)
    # Sofortige Bedienung: Radar + Mond, damit die App den Marker gleich fuellen kann
    rep = ac.SiteReport(name=name, lat=body.lat, lon=body.lon)
    await ac.scrape_radar(body.lat, body.lon, rep)
    ac.attach_moon(rep)
    log.info("[API] Watch gesetzt: %s (%.3fh)", name, body.hours)
    return {"ok": True, "name": name, "expires": expires,
            "radar_status": rep.radar_status, "precip_2h": rep.precip_2h,
            "wind_speed": rep.wind_speed,
            "dewpoint_spread": rep.dewpoint_spread,
            "moon": ac.moon_cached(body.lat, body.lon)}


@app.delete("/api/watch")
async def api_unwatch(request: Request):
    """Alle Live-Standorte entfernen."""
    if API_TOKEN and request.headers.get("x-api-token") != API_TOKEN:
        raise HTTPException(401, "Ungueltiger API-Token")
    with ac.watchlist_lock():
        n = len(ac.load_watchlist())
        ac.save_watchlist([])
    return {"ok": True, "removed": n}


# --- Warnungen: DWD-Geoserver-Polygone als GeoJSON fuer den Karten-Layer ---
_WARNS_CACHE = {"ts": 0.0, "data": None}
_WARNS_TTL = 60  # Sekunden; der Radar-Timer zieht eh alle 5 Min frisch


@app.get("/api/warnings")
def api_warnings():
    """Aktive DWD-Unwetterwarnungen der Gesamtregion (aller Standorte) als
    GeoJSON - Leaflet zeichnet die Polygone direkt. Der Crawler prueft dieselbe
    Quelle per Punkt-in-Polygon; hier gehen die Geometrien 1:1 durch."""
    now = time.time()
    if _WARNS_CACHE["data"] is not None and now - _WARNS_CACHE["ts"] < _WARNS_TTL:
        return _WARNS_CACHE["data"]

    locs = ac.active_locations(ac.DEFAULT_LOCATIONS)
    lats = [l["lat"] for l in locs] + [l["lat"] for l in ac.load_watchlist()]
    lons = [l["lon"] for l in locs] + [l["lon"] for l in ac.load_watchlist()]
    # BBOX um alle Standorte + Puffer (~25 km), desselbe Schema wie im Crawler
    bbox = (f"{min(lons) - 0.35:.4f},{min(lats) - 0.28:.4f},"
            f"{max(lons) + 0.35:.4f},{max(lats) + 0.28:.4f},EPSG:4326")
    url = f"{ac.DWD_WFS_URL}&bbox={urllib.parse.quote(bbox)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ac.USER_AGENT})
        raw = json.loads(urllib.request.urlopen(req, timeout=15).read())
        features = []
        for f in raw.get("features", []):
            p = f.get("properties", {}) or {}
            event = (p.get("EVENT") or "").upper()
            if not event:
                continue
            # Fuer die Karte relevant: Gewitter/Regen farblich hervorheben
            kind = ("storm" if any(k in event for k in ac.STORM_KEYWORDS)
                    else "rain" if any(k in event for k in ac.RAIN_KEYWORDS)
                    else "other")
            features.append({
                "type": "Feature",
                "geometry": f.get("geometry"),
                "properties": {"event": event, "kind": kind,
                               "severity": p.get("SEVERITY", ""),
                               "description": (p.get("DESCRIPTION") or "")[:300],
                               "start": p.get("ONSET") or p.get("START"),
                               "end": p.get("EXPIRES") or p.get("END")},
            })
        data = {"type": "FeatureCollection", "features": features}
        _WARNS_CACHE.update(ts=now, data=data)
        return data
    except Exception as e:
        log.warning("[API] DWD-Warnungen abfragen fehlgeschlagen: %s",
                    type(e).__name__)
        if _WARNS_CACHE["data"] is not None:
            return _WARNS_CACHE["data"]
        raise HTTPException(503, f"DWD-WFS nicht erreichbar ({type(e).__name__})")


# --- Lichtverschmutzungs-Layer: Proxy mit permanentem Disk-Cache ---
from lpcache import get_lp_tile  # noqa: E402


@app.get("/api/lp-tiles/{z}/{x}/{y}")
def api_lp_tile(z: int, x: int, y: int):
    return get_lp_tile(z, x, y)


# --- Regen-Raster: Open-Meteo multi-coordinate, zoomgekoppelt, RAM-Cache ---
# Basis fuer die Icon-Darstellung im Frontend (Wolke/Tropfen statt Heatmap).
_RAINGRID_CACHE: dict = {}          # "lat,lon" -> (mono_ts, punkt-dict)
_RAINGRID_TTL = 900                 # OM current aktualisiert ~15 min
_RAINGRID_MAX_PTS = 120
# Zoom -> Grad-Abstand (~10 km bei 0.10 auf 49.5 N); groessere Zooms nutzen
# das feinste Raster, kleinere verdichten den Schritt
_RAINGRID_STEP = {8: 0.25, 9: 0.15, 10: 0.10, 11: 0.07}


@app.get("/api/rain-grid")
def api_rain_grid(bbox: str, zoom: int = 9):
    """Niederschlag fuer ein Gitter ueber den sichtbaren Kartenausschnitt.
    EIN OM-Request bedient alle fehlenden Gitterpunkte (comma-separated
    coordinates); Punkte liegen auf einem globalen Raster, damit Pan/Zoom
    den Zell-Cache trifft statt neue Koordinaten zu erzeugen."""
    try:
        p = [float(x) for x in bbox.split(",")]
        assert len(p) == 4
    except (ValueError, TypeError, AssertionError):
        raise HTTPException(400, "bbox=lat1,lon1,lat2,lon2 noetig")
    s, w = min(p[0], p[2]), min(p[1], p[3])
    n, e = max(p[0], p[2]), max(p[1], p[3])

    step = _RAINGRID_STEP.get(min(max(zoom, 8), 11), 0.15)
    while True:
        i_s, i_n = int(math.ceil(s / step)), int(n // step)
        j_w, j_e = int(math.ceil(w / step)), int(e // step)
        if (i_n - i_s + 1) * (j_e - j_w + 1) <= _RAINGRID_MAX_PTS:
            break
        step = round(step * 2, 4)   # zu viele Punkte: Raster groeber

    now = time.time()
    pts, missing = [], []
    for i in range(i_s, i_n + 1):
        for j in range(j_w, j_e + 1):
            lat, lon = round(i * step, 4), round(j * step, 4)
            key = f"{lat:.4f},{lon:.4f}"
            cached = _RAINGRID_CACHE.get(key)
            if cached and now - cached[0] < _RAINGRID_TTL:
                pts.append(cached[1])
            else:
                missing.append((lat, lon, key))
    if missing:
        url = ("https://api.open-meteo.com/v1/forecast?latitude="
               + ",".join(f"{m[0]:.4f}" for m in missing)
               + "&longitude=" + ",".join(f"{m[1]:.4f}" for m in missing)
               + "&current=precipitation,precipitation_probability,weathercode"
                 "&hourly=precipitation,precipitation_probability"
                 "&forecast_hours=7&timezone=Europe%2FBerlin")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ac.USER_AGENT})
            data = json.loads(urllib.request.urlopen(req, timeout=12).read())
            items = data if isinstance(data, list) else [data]
            if len(items) != len(missing):
                raise ValueError("Anzahl-Antwort != Anfrage")
            for (lat, lon, key), it in zip(missing, items):
                cur = it.get("current") or {}
                # 7-h-Serie (aktuelle Stunde + 6) fuer den Zeitregler im
                # Frontend: Icons pro Reglerstellung aus derselben Antwort
                h_times = (it.get("hourly") or {}).get("time") or []
                h_mm = (it.get("hourly") or {}).get("precipitation") or []
                h_pp = (it.get("hourly") or {}).get("precipitation_probability") or []
                hours = [{"h": i, "mm": h_mm[i] if i < len(h_mm) else None,
                          "prob": h_pp[i] if i < len(h_pp) else None}
                         for i in range(min(7, len(h_times)))]
                d = {"lat": lat, "lon": lon,
                     "mm": cur.get("precipitation"),
                     "prob": cur.get("precipitation_probability"),
                     "code": cur.get("weathercode"),
                     "hours": hours}
                _RAINGRID_CACHE[key] = (now, d)
                pts.append(d)
        except Exception as ex:
            log.warning("[API] Regen-Raster OM-Abfrage fehlgeschlagen: %s (%d Punkte)",
                        type(ex).__name__, len(missing))
            # Cache-Treffer zurueckgeben, was da ist; Frontend zeigt Rest naechsten Takt
    # Cache-Begrenzung: grob aufräumen (>4x Maximalbedarf)
    if len(_RAINGRID_CACHE) > 4 * _RAINGRID_MAX_PTS * 2:
        for k in sorted(_RAINGRID_CACHE, key=lambda k: _RAINGRID_CACHE[k][0])[:len(_RAINGRID_CACHE) // 2]:
            _RAINGRID_CACHE.pop(k, None)
    return {"step": step, "ts": now,
            "points": sorted(pts, key=lambda d: (d["lat"], d["lon"]))}


# --- Vorausschau: stündliche Reihe + Golden Window (latest-wins JSON) ---
# --- Observable: was ist mit dem Equipment JETZT prinzipiell sichtbar ---
@app.get("/api/observable")
def api_observable(id: str, equipment: str,
                   camera: Optional[str] = None,
                   seeing: Optional[float] = None):
    """Filtert den Objektkatalog nach Hoehe/Grenzgroesse/Nacht.
    id= Standort (locations.json, liefert bortle_class), equipment=
    Teleskop-Key, camera= optionaler Kamera-Key (guiding-Kameras werden
    ignoriert), seeing= optional - sonst letzte Heavy-Messung des
    Standorts aus der DB."""
    import sys as _sys
    _sys.path.insert(0, "/home/enigma")
    from app.engine import equipment as eq_mod
    from app.engine import observable_filter as of

    try:
        locs = ac.active_locations(ac.DEFAULT_LOCATIONS) \
            + ac.load_watchlist()
    except Exception as e:  # noqa: BLE001 - sichtbar geloggt
        log.warning("[API] Standort-Lookup fehlgeschlagen: %s",
                    type(e).__name__)
        locs = []
    loc = next((l for l in locs if l.get("id") == id), None)
    if loc is None:
        raise HTTPException(404, f"Kein Standort mit id '{id}'")

    try:
        inventory = eq_mod.load_equipment()
        eq = eq_mod.build_equipment(inventory, equipment, camera)
    except (SystemExit, KeyError, ValueError) as e:
        raise HTTPException(400, f"Equipment ungueltig: {e}")

    seeing_val = seeing
    if seeing_val is None:
        try:
            conn = _db()
            row = conn.execute(
                "SELECT seeing FROM crawls WHERE location_name=? "
                "AND seeing IS NOT NULL ORDER BY ts DESC LIMIT 1",
                (loc["name"],)).fetchone()
            conn.close()
            seeing_val = row[0] if row else None
        except Exception as e:  # noqa: BLE001 - sichtbar geloggt
            log.warning("[API] Seeing-Lookup fehlgeschlagen: %s",
                        type(e).__name__)

    from datetime import datetime as _dtm, timezone as _tzm
    return of.observation_summary(
        eq, int(loc.get("bortle_class", 6)), seeing_val,
        _dtm.now(_tzm.utc), loc["lat"], loc["lon"])


# --- Bias-Transparenz: aktuelle Werte (V1) + Zeitreihe (V2) ---
def _bias_stats_payload() -> Optional[dict]:
    try:
        with open(getattr(ac, "BIAS_PATH",
                          "/home/enigma/.astro_crawler_bias.json"),
                  "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    buckets = {}
    for param in ("clouds", "seeing"):
        for bucket, entry in (raw.get(param) or {}).items():
            buckets[f"{param}_{bucket}h"] = {
                "bias": entry.get("bias"),
                "sample_n": entry.get("n"),
                "bias_7d": entry.get("bias_7d"),
                "n_7d": entry.get("n_7d"),
                "min_n_threshold": raw.get("min_n"),
                "applied": entry.get("bias") is not None,
            }
    return {
        "computed_at": raw.get("computed_at"),
        "buckets": buckets,
        "applied_to": "/api/forecast (display layer only)",
        "not_applied_to": "rating, golden_window (intentional)",
    }


@app.get("/api/bias-stats")
def api_bias_stats():
    payload = _bias_stats_payload()
    if payload is None:
        raise HTTPException(503, "Bias-Daten noch nicht berechnet "
                                 "(naechster taeglicher Lauf liefert sie)")
    return payload


@app.get("/api/bias-history")
def api_bias_history(days: int = Query(30, ge=1, le=365)):
    try:
        conn = _db()
        cutoff = (dt.datetime.now()
                  - dt.timedelta(days=days)).isoformat(timespec="seconds")
        rows = conn.execute(
            "SELECT computed_at, bucket, bias, sample_n, bias_7d, n_7d "
            "FROM bias_history "
            "WHERE computed_at >= ? ORDER BY computed_at DESC, bucket",
            (cutoff,)).fetchall()
        conn.close()
    except Exception as e:  # noqa: BLE001 - sichtbar geloggt
        log.warning("[API] bias-history fehlgeschlagen: %s", type(e).__name__)
        raise HTTPException(503, "bias_history nicht verfuegbar")
    return [{"computed_at": r[0], "bucket": r[1], "bias": r[2],
             "sample_n": r[3],
             "bias_7d": r[4] if len(r) > 4 else None,
             "n_7d": r[5] if len(r) > 5 else None} for r in rows]


_TRANSIT_BBOX = (48.8, 7.8, 50.2, 9.5)  # VRN-Gebiet um Mannheim/Pfalz


# HQ Ilvesheim: Equipment-Depot (Teleskop & Crawler) - fester Startpunkt
# aller Transit-Einsatzwege, kein dynamisches Nutzer-GPS mehr.
HQ_ILVESHEIM = {"lat": 49.4783726, "lon": 8.5662896}

_GTFS_CACHE = {}


def _transit_source(service_day):
    """GTFS-Quelle pro Service-Datum cachen: der 153MB-Feed wird sonst bei
    jedem /api/deployment-Aufruf neu geparst (~8s)."""
    import sys as _sys
    _sys.path.insert(0, "/home/enigma")
    from app.sources.transit import GTFSStaticSource
    src = _GTFS_CACHE.get(service_day)
    if src is None:
        src = GTFSStaticSource("/home/enigma/gtfs", bbox=_TRANSIT_BBOX,
                               service_date=service_day)
        _GTFS_CACHE.clear()   # nur den jeweils aktuellen Service-Tag halten
        _GTFS_CACHE[service_day] = src
    return src


@app.get("/api/deployment")
def api_deployment(id: str, home: str = "Ilvesheim HQ",
                   home_lat: float = HQ_ILVESHEIM["lat"],
                   home_lon: float = HQ_ILVESHEIM["lon"],
                   setup_minutes: int = 30):
    """OePNV-Deployment-Plan fuer einen Standort: letzte Bahn hin (um
    rechtzeitig vor dem Golden Window + Aufbau-Puffer da zu sein),
    frueheste Bahn zurueck + Abbau-Warnung. Primaerquelle VRN-GTFS-Static
    (lokal ~/gtfs), DELFI-Fallback dokumentiert."""
    import sys as _sys
    _sys.path.insert(0, "/home/enigma")
    from app.engine.deployment import deployment_window
    from app.sources.transit import GTFSNotAvailableError
    from datetime import datetime as _dt, timedelta as _td

    try:
        locs = ac.active_locations(ac.DEFAULT_LOCATIONS) \
            + ac.load_watchlist()
    except Exception as e:  # noqa: BLE001 - sichtbar geloggt
        log.warning("[API] Standort-Lookup fehlgeschlagen: %s",
                    type(e).__name__)
        locs = []
    obs = next((l for l in locs if l.get("id") == id
                or l.get("name") == id), None)
    if obs is None:
        raise HTTPException(404, f"Kein Standort mit id '{id}'")
    home_loc = next((l for l in locs
                     if l.get("id") == home or l.get("name") == home), None)
    if home_loc is None:
        # HQ-Default: Ilvesheim (per home_lat/home_lon ueberschreibbar)
        home_loc = {"id": "hq_ilvesheim", "name": "Ilvesheim HQ",
                    "lat": home_lat, "lon": home_lon}

    # Golden Window aus dem Forecast (naechstes Fenster)
    try:
        with open(ac.FORECAST_PATH, "r", encoding="utf-8") as f:
            fc = json.load(f)
        entry = fc.get(obs["name"]) or {}
    except (OSError, ValueError):
        entry = {}
    gws = entry.get("golden_windows") or []
    gw = gws[0] if gws else None
    # kein GW: Route ab JETZT statt 404 (Fix 30.09.)

    now = _dt.now()
    from app.engine.deployment import resolve_window_times
    gws_dt, gwe_dt, from_now = resolve_window_times(gw, now)

    # Service-Datum = Abend des Golden Windows; bei Route-ab-jetzt
    # (Fenster vorbei/kein GW) zaehlt der heutige Abend-Service.
    service_day = (now if from_now else
                   _dt.fromisoformat(gw["night"])).date()
    try:
        transit = _transit_source(service_day)
    except GTFSNotAvailableError as e:
        raise HTTPException(503, str(e))

    _night = _dt.fromisoformat(gw["night"]) if gw else now
    log.info("[API] deployment %s: night=%s gw=%s-%s service_day=%s "
             "home=%s from_now=%s", id, _night.date(),
             gws_dt.strftime("%H:%M"), gwe_dt.strftime("%H:%M"),
             service_day, home_loc.get("name"), from_now)
    # Dynamic Abort: Wenn das Fenster JETZT laeuft, die bias-korrigierte
    # Prognose der kommenden Stunden im Fenster pruefen. Kippt eine Stunde
    # auf NO-GO (z.B. Wolken ziehen auf), wird die Rueckfahrt-Suche auf
    # diesen Umschlagpunkt vorverlegt - statt stur bis Fensterende.
    abort_at = None
    abort_reason = None
    if gws_dt <= now < gwe_dt and entry.get("series"):
        try:
            profile = ac.get_profile(ac.load_state())
        except Exception:  # noqa: BLE001 - Profil-Default reicht
            profile = "dso"
        series, _bias_info = _apply_bias_to_series(
            entry["series"], _load_bias(), now)
        for h in series:
            try:
                ts = _dt.datetime.fromisoformat(h.get("ts", ""))
            except (ValueError, TypeError):
                continue
            if ts < now or ts >= gwe_dt:
                continue
            ok, reasons = ac._hour_score(h, profile)
            if not ok:
                abort_at = ts
                abort_reason = ", ".join(reasons)
                log.info("[API] DYNAMIC ABORT %s: Stunde %s no-go (%s)",
                         id, ts.strftime("%H:%M"), abort_reason)
                break

    plan = deployment_window(gws_dt, gwe_dt, home_loc, obs, transit,
                             setup_minutes, abort_at=abort_at)
    note = None
    if not plan.latest_departure:
        note = ("Keine OePNV-Verbindung fuer dieses Zeitfenster gefunden "
                "(Spaet-/Nachtzeit ohne Bedienung). Auto/Taxi einplanen.")
    elif not plan.extraction:
        note = ("Keine Rueckverbindung per OePNV nach Fensterende. "
                "Rueckfahrt per Auto/Taxi organisieren.")
    return {
        "destination": plan.destination,
        "golden_window": f"{plan.golden_window_start}-{plan.golden_window_end}",
        "golden_window_night": gw.get("night"),
        "latest_departure": plan.latest_departure,
        "extraction": plan.extraction,
        "extraction_warning_ts": plan.extraction_warning_ts,
        "setup_buffer_min": plan.setup_buffer_min,
        "transit_source": plan.transit_source,
        "home": home_loc.get("name", "?"),
        "from_now": from_now,
        **({"note": ("Golden Window liegt zurueck bzw. fehlt - "
                     "Route ab jetzt berechnet." + (" " + note if note else ""))}
           if from_now else {"note": note}),
        **({"dynamic_abort": {**plan.dynamic_abort,
                              "reason": abort_reason}}
           if plan.dynamic_abort else {}),
    }



# --- Phase 2: Native-APK-Download (statisch, token-gated wie fwhw_sync) ---
APK_PATH = "/home/enigma/astro-app/native/android/app/build/outputs/apk/debug/app-debug.apk"


@app.get("/native/astro-cortex.apk")
def api_native_apk(token: Optional[str] = None, request: Request = None):
    """Debug-APK der Capacitor-Shell (Phase 2). Gleicher Token-Mechanismus
    wie fwhw_sync - per Header x-api-token ODER Query-Parameter (fuer
    einfache Browser-Downloads ueber Tailscale). Kein Token konfiguriert
    = Endpunkt offen (bewusst, Heimnetz)."""
    if API_TOKEN and (request.headers.get("x-api-token") != API_TOKEN
                      and token != API_TOKEN):
        raise HTTPException(401, "Ungueltiger API-Token")
    if not os.path.exists(APK_PATH):
        raise HTTPException(404, "APK nicht gebaut - native/setup_native.sh"
                                 " + gradlew assembleDebug ausfuehren")
    return FileResponse(APK_PATH, media_type="application/vnd.android.package-archive",
                        filename="astro-cortex.apk")



# --- Cloudflare-Access-Auth-Bridge fuer die native App ---
# Die App navigiert (bei abgelaufenem CF_Authorization) hierher; Access
# erzwingt Email-OTP im selben WebView, danach kehrt diese Seite zur
# App-Origin https://localhost zurueck - Cookie bleibt im WebView-Glas.
AUTH_BRIDGE_HTML = """<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ASTRO CC - AUTH</title>
<meta http-equiv="refresh" content="1;url=https://localhost/">
<style>
 body { background:#07090c; color:#3aff7c; font-family:monospace;
        display:flex; align-items:center; justify-content:center;
        height:100vh; margin:0; text-align:center; }
 a { color:#ffd24a; font-size:18px; }
 small { color:#5d7186; display:block; margin-top:24px; }
</style></head><body>
 <div><b>ASTRO CC // AUTH OK</b><br>
  <a href="https://localhost/">&#9650; ZURUECK ZUR APP</a>
  <small>Browser-Nutzer: dieses Tab kann geschlossen werden -<br>
  die App ist ueber ihre gewohnte Adresse erreichbar.</small>
 </div>
</body></html>"""


@app.get("/auth/mobile")
def api_auth_mobile():
    """Bruecke nach dem Cloudflare-Access-Login (siehe AUTH_BRIDGE_HTML).
    Erreicht den Ursprung NUR mit gueltigem CF_Authorization-Cookie."""
    return HTMLResponse(AUTH_BRIDGE_HTML)



# --- OTA-Live-Updates: Web-Bundle + Manifest fuer die native App ---
UPDATES_DIR = os.path.expanduser("~/updates")


@app.middleware("http")
async def updates_cors(request, call_next):
    # Muss NACH allen anderen Middlewares registriert werden = aeusserste
    # Schicht. Setzt Header explizit (ueberschreibt CORSMiddleware fuer
    # /updates-Pfade, damit der Updater immer ACAO: * sieht).
    """OTA-Routen brauchen bedingungsloses CORS: Der Capacitor-Updater
    laedt bundle.zip ohne unsere Token-Header (CF-Bypass aktiv) und
    blockt ohne Access-Control-Allow-Origin: * den Download."""
    if request.url.path.startswith("/updates"):
        if request.method == "OPTIONS":
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse("", headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Max-Age": "600"})
        resp = await call_next(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "*"
        return resp
    return await call_next(request)


@app.get("/updates/manifest.json")
@app.get("/updates/latest.json")
def api_ota_manifest():
    """OTA-Manifest (Version + Bundle-URL). Erzeugt vom Deploy-Skript
    nach jedem Frontend-Deploy; Cloudflare Access schuetzt den Pfad."""
    mf = os.path.join(UPDATES_DIR, "latest.json")
    if not os.path.exists(mf):
        raise HTTPException(404, "kein OTA-Bundle deployt")
    return FileResponse(mf, media_type="application/json",
                        headers={"Cache-Control": "no-store"})


@app.get("/updates/bundle.zip")
def api_ota_bundle():
    """Aktuelles Frontend-Bundle (ZIP, Wurzel = Web-Assets) fuer den
    @capgo/capacitor-updater."""
    zp = os.path.join(UPDATES_DIR, "bundle.zip")
    if not os.path.exists(zp):
        raise HTTPException(404, "kein OTA-Bundle deployt")
    return FileResponse(zp, media_type="application/zip",
                        headers={"Cache-Control": "no-store"})



# --- Ground Truth: menschliche Bodenbeobachtung (Datenhoheit Phase 2) ---
class GroundTruthBody(BaseModel):
    timestamp: Optional[str] = None   # ISO; Default = Server-Jetztzeit
    reporter: str = "balkon"          # Anonymisiert (keine echten Namen)
    actual_clouds: int                # 0-100
    location_name: Optional[str] = None
    note: Optional[str] = None        # z.B. "Inversion", "Rauchschicht"


@app.post("/api/telemetry/ground_truth")
def api_ground_truth(body: GroundTruthBody):
    """Menschliche Bodenwahrheit (bricht die zirkulaere DWD-Verifikation).
    Speichert den Report UND matcht sofort gegen die letzte Prognose,
    um das Delta sichtbar zu machen. Keine extra Auth noetig - der
    Request kommt durch CF Service Tokens."""
    import sqlite3 as _sql
    from datetime import datetime as _dt

    ts = body.timestamp or _dt.now().isoformat(timespec="seconds")
    # Coverage-Wert klemmen
    clouds = max(0, min(100, body.actual_clouds))

    # SQLite: Tabelle anlegen + einfuegen
    conn = _sql.connect("/home/enigma/.astro_crawler.db")
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ground_truth_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                reporter TEXT NOT NULL DEFAULT 'balkon',
                actual_clouds INTEGER NOT NULL,
                location_name TEXT,
                note TEXT,
                matched_forecast_clouds INTEGER,
                delta INTEGER,
                created_at TEXT NOT NULL
            )""")
        # Sofortiges Matching: letzte Prognose fuer diese Stunde/Ort
        matched_fc = None
        delta = None
        hour_key = ts[:13]
        if body.location_name:
            row = conn.execute("""
                SELECT clouds_total FROM forecast_log
                WHERE location_name = ? AND target_ts LIKE ?
                ORDER BY lead_hours ASC LIMIT 1
            """, (body.location_name, hour_key + "%")).fetchone()
            if row and row[0] is not None:
                matched_fc = row[0]
                delta = round(matched_fc - clouds, 1)
        created = _dt.now().isoformat(timespec="seconds")
        conn.execute("""
            INSERT INTO ground_truth_log
                (ts, reporter, actual_clouds, location_name, note,
                 matched_forecast_clouds, delta, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (ts, body.reporter, clouds, body.location_name, body.note,
              matched_fc, delta, created))
        conn.commit()
        gt_id = conn.execute(
            "SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()

    log.info("[GroundTruth] #%d: %s%% von %s%s%s", gt_id, clouds,
             body.reporter,
             f" @ {body.location_name}" if body.location_name else "",
             f" (Prognose war {matched_fc}%, Delta {delta:+.0f})"
             if matched_fc is not None else "")
    return {
        "ok": True, "id": gt_id, "actual_clouds": clouds,
        "matched_forecast": matched_fc, "delta": delta,
        "reporter": body.reporter}


@app.get("/api/telemetry/ground_truth")
def api_ground_truth_list(limit: int = 100):
    """Letzte Ground-Truth-Reports fuer Transparenz/Analyse."""
    import sqlite3 as _sql
    conn = _sql.connect("/home/enigma/.astro_crawler.db")
    try:
        conn.row_factory = _sql.Row
        rows = conn.execute("""
            SELECT id, ts, reporter, actual_clouds, location_name, note,
                   matched_forecast_clouds, delta
            FROM ground_truth_log ORDER BY id DESC LIMIT ?
        """, (min(limit, 500),)).fetchall()
        return {"reports": [dict(r) for r in rows]}
    except _sql.OperationalError:
        return {"reports": []}
    finally:
        conn.close()





# --- Luecke 2: Wolken-Bewegungsvektor aus dem Regen-Raster ---
@app.get("/api/weather/movement")
def api_weather_movement():
    """Berechnet den Bewegungsvektor von Niederschlags-/Wolkenmustern
    aus dem Open-Meteo-Regenraster (gleiche Datenquelle wie das
    Frontend-Regen-Icon-Raster). Vergleicht zwei Zeitschritte und
    liefert Richtung (Grad von Nord), Geschwindigkeit (km/h) und
    Trend (clearing/clouding/stable). Nutzt Kreuzkorrelation der
    2D-Niederschlagsfelder fuer die Verschiebungsschaetzung."""
    import numpy as np
    from datetime import datetime as _dt, timedelta as _td

    # Zentrum: Mannheim (grob)
    lat_c, lon_c = 49.5, 8.6
    grid_size = 7
    # Grid-Ausdehnung ~0.15 Grad (~12km) pro Zelle
    step = 0.15

    async def fetch_grid(hour_offset):
        """7x7 Grid mit Gesamtbewoelkung fuer einen Zeitpunkt."""
        coords = []
        for dy in range(-grid_size // 2, grid_size // 2 + 1):
            for dx in range(-grid_size // 2, grid_size // 2 + 1):
                coords.append(f"{lat_c + dy * step:.3f},{lon_c + dx * step:.3f}")
        url = ("https://api.open-meteo.com/v1/forecast"
               f"?latitude={lat_c}&longitude={lon_c}"
               "&hourly=cloud_cover"
               f"&forecast_days=2&timezone=auto")
        try:
            import urllib.request, json as _json
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = _json.loads(r.read())
            times = data.get("hourly", {}).get("time", [])
            clouds = data.get("hourly", {}).get("cloud_cover", [])
            now_idx = next((i for i, t in enumerate(times)
                          if t[:13] >= _dt.now().strftime("%Y-%m-%dT%H")), 0)
            idx = min(now_idx + hour_offset, len(clouds) - 1)
            return clouds[idx] if idx < len(clouds) else None
        except Exception:
            return None

    # Einfacher Ansatz: Vergleich von Gesamtbewoelkung jetzt vs. +2h
    # am Standort + Bodenwindrichtung als Proxy fuer Bewegungsrichtung
    now_cloud = None
    future_cloud = None
    wind_dir = None
    wind_speed = None
    try:
        import urllib.request, json as _json
        url = ("https://api.open-meteo.com/v1/forecast"
               f"?latitude={lat_c}&longitude={lon_c}"
               "&hourly=cloud_cover,wind_speed_10m,wind_direction_10m"
               "&forecast_days=1&timezone=auto")
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = _json.loads(r.read())
        times = data.get("hourly", {}).get("time", [])
        clouds = data.get("hourly", {}).get("cloud_cover", [])
        ws = data.get("hourly", {}).get("wind_speed_10m", [])
        wd = data.get("hourly", {}).get("wind_direction_10m", [])
        now_idx = next((i for i, t in enumerate(times)
                      if t[:13] >= _dt.now().strftime("%Y-%m-%dT%H")), 0)
        if now_idx < len(clouds):
            now_cloud = clouds[now_idx]
            fut_idx = min(now_idx + 2, len(clouds) - 1)
            future_cloud = clouds[fut_idx]
            wind_speed = ws[now_idx] if now_idx < len(ws) else None
            wind_dir = wd[now_idx] if now_idx < len(wd) else None
    except Exception:
        pass

    if now_cloud is None or future_cloud is None:
        return {"available": False, "reason": "Keine Open-Meteo-Daten"}

    # Trend bestimmen
    delta = future_cloud - now_cloud
    if delta < -15:
        trend = "clearing"
        trend_txt = "Aufklarend"
    elif delta > 15:
        trend = "clouding"
        trend_txt = "Eintruebend"
    else:
        trend = "stable"
        trend_txt = "Stabil"

    # Bewegungsrichtung: Windrichtung als Proxy (Wolken bewegen sich
    # mit dem Wind). Windrichtung ist die Richtung, aus der der Wind
    # KOMMT -> Bewegungsrichtung = 180 + wind_dir
    movement_dir = (wind_dir + 180) % 360 if wind_dir is not None else None

    # Standort-spezifische Vorhersage: welche Standorte werden besser/schlechter?
    locs = {}
    try:
        for l in ac.active_locations(ac.DEFAULT_LOCATIONS):
            name = l["name"]
            lat, lon = l["lat"], l["lon"]
            # Einfache Geometrie: Ist der Standort in Bewegungsrichtung?
            if movement_dir is not None and wind_speed and wind_speed > 3:
                # Vektor vom Zentrum zum Standort
                import math
                dlat = lat - lat_c
                dlon = (lon - lon_c) * math.cos(math.radians(lat_c))
                bearing_to_loc = (math.degrees(math.atan2(dlon, dlat)) + 360) % 360
                ang_diff = abs(bearing_to_loc - movement_dir)
                if ang_diff > 180:
                    ang_diff = 360 - ang_diff
                # Wenn Standort in Bewegungsrichtung (<60 Grad Abweichung)
                # und Trend=clearing -> Standort klart eher auf
                in_path = ang_diff < 60
                dist_km = math.sqrt(dlat**2 + dlon**2) * 111
                eta_h = dist_km / max(wind_speed * 3.6, 1) if wind_speed else None
                locs[name] = {
                    "in_clearing_path": trend == "clearing" and in_path,
                    "distance_km": round(dist_km, 1),
                    "eta_hours": round(eta_h, 1) if eta_h else None,
                }
    except Exception:
        pass

    return {
        "available": True,
        "current_clouds": now_cloud,
        "clouds_2h": future_cloud,
        "delta": delta,
        "trend": trend,
        "trend_text": trend_txt,
        "wind_direction_deg": wind_dir,
        "wind_speed_kmh": round(wind_speed * 3.6, 1) if wind_speed else None,
        "movement_direction_deg": movement_dir,
        "locations": locs,
        "timestamp": _dt.now().isoformat(timespec="seconds"),
    }



# --- L3: Cloud-Hunter — dynamischer Umkreis-Scan ---
CLOUD_HUNTER_CENTER = (49.48, 8.63)   # Mannheim/Heidelberg
LIGHT_POLLUTION_SOURCES = [
    # (lat, lon, radius_km, bortle_max) — Staedte mit Lichtglocke
    (49.488, 8.466, 8, 8),   # Mannheim/Ludwigshafen
    (49.588, 8.664, 5, 7),   # Weinheim
    (49.526, 8.572, 4, 7),   # Viernheim
    (49.401, 8.676, 5, 7),   # Heidelberg
    (49.634, 8.357, 4, 7),   # Worms
]


def _estimate_bortle(lat, lon):
    """Schaetzt Bortle-Klasse nach Distanz zu Lichtquellen."""
    worst = 3   # ländlicher Standard
    for src_lat, src_lon, radius, bortle in LIGHT_POLLUTION_SOURCES:
        d = _haversine_km(lat, lon, src_lat, src_lon)
        if d < radius:
            worst = max(worst, bortle)
        elif d < radius * 2:
            worst = max(worst, bortle - 1)
    return worst


def _haversine_km(lat1, lon1, lat2, lon2):
    import math
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + \
        math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@app.get("/api/cloud-hunter")
def api_cloud_hunter(radius_km: int = 50, hours_ahead: int = 3,
                     max_bortle: int = 6):
    """Dynamischer Umkreis-Scan: Wo ist der klarste Himmel in den
    naechsten N Stunden? Ein Batch-Call an Open-Meteo mit bis zu 100
    Grid-Punkten (10km Raster), gefiltert nach Lichtverschmutzung.
    Liefert Grid + besten Spot (DYNAMIC OPTIMUM)."""
    import math
    import urllib.request
    import json as _json
    from datetime import datetime as _dt, timedelta as _td

    center_lat, center_lon = CLOUD_HUNTER_CENTER

    # Grid: ~10km Raster im Umkreis
    lat_step = 10 / 111.0
    lon_step = 10 / (111.0 * math.cos(math.radians(center_lat)))
    n_lat = int(radius_km / 10) * 2 + 1
    n_lon = int(radius_km / (10 * math.cos(math.radians(center_lat)))) * 2 + 1

    points = []
    for i in range(n_lat):
        for j in range(n_lon):
            lat = center_lat + (i - n_lat // 2) * lat_step
            lon = center_lon + (j - n_lon // 2) * lon_step
            dist = _haversine_km(center_lat, center_lon, lat, lon)
            if dist > radius_km:
                continue
            bortle = _estimate_bortle(lat, lon)
            if bortle > max_bortle:
                continue
            points.append({"lat": round(lat, 3), "lon": round(lon, 3),
                           "bortle": bortle, "dist_km": round(dist, 1)})
    # Auf 100 begrenzen (Open-Meteo Batch-Limit)
    points = points[:100]
    if not points:
        return {"grid": [], "best": None,
                "reason": "Keine Punkte im Umkreis mit Bortle<=" + str(max_bortle)}

    # Batch-Call an Open-Meteo
    lats = ",".join(str(p["lat"]) for p in points)
    lons = ",".join(str(p["lon"]) for p in points)
    url = (f"https://api.open-meteo.com/v1/forecast"
           f"?latitude={lats}&longitude={lons}"
           f"&hourly=cloud_cover,wind_speed_10m"
           f"&forecast_hours={hours_ahead}&timezone=auto")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "astro-cc"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = _json.loads(r.read())
    except Exception as e:
        return {"grid": [], "best": None, "reason": f"Open-Meteo: {e}"}

    # Antwort kann ein Objekt (1 Location) oder Array sein
    results = data if isinstance(data, list) else [data]
    grid = []
    now_hour = _dt.now().strftime("%Y-%m-%dT%H:00")
    for idx, point in enumerate(points):
        if idx >= len(results):
            break
        hourly = results[idx].get("hourly", {})
        times = hourly.get("time", [])
        clouds = hourly.get("cloud_cover", [])
        winds = hourly.get("wind_speed_10m", [])
        # Mittel ueber die naechsten N Stunden
        start_i = next((i for i, t in enumerate(times)
                       if t >= now_hour), 0)
        window_clouds = [c for c in clouds[start_i:start_i + hours_ahead]
                        if c is not None]
        window_winds = [w for w in winds[start_i:start_i + hours_ahead]
                       if w is not None]
        if not window_clouds:
            continue
        avg_clouds = sum(window_clouds) / len(window_clouds)
        avg_wind = sum(window_winds) / len(window_winds) if window_winds else 0
        grid.append({**point,
                     "clouds_avg": round(avg_clouds, 0),
                     "wind_avg": round(avg_wind, 1),
                     "score": round(avg_clouds + point["bortle"] * 3
                                    + avg_wind * 0.5, 1)})

    # Besten Spot finden (niedrigster Score = klar + dunkel + windstill)
    best = min(grid, key=lambda g: g["score"]) if grid else None
    return {
        "grid": grid,
        "best": best,
        "parameters": {"radius_km": radius_km, "hours_ahead": hours_ahead,
                       "max_bortle": max_bortle,
                       "center": {"lat": center_lat, "lon": center_lon}},
        "timestamp": _dt.now().isoformat(timespec="seconds"),
    }


# --- L4: Departure-Optimizer — Time-to-Target Deadline-Engine ---
@app.get("/api/departure-optimizer")
def api_departure_optimizer(setup_minutes: int = 45):
    """Koppelt Golden Windows mit Transit-Zeiten. Fuer jeden Standort:
    Fenster-Start -> Ruestzeit abziehen -> Transitzeit abziehen ->
    Latest Departure. Liefert Countdown + Erreichbarkeit."""
    from datetime import datetime as _dt, timedelta as _td
    import math

    now = _dt.now()
    HQ_LAT, HQ_LON = 49.4783726, 8.5662896

    try:
        with open(ac.FORECAST_PATH, "r", encoding="utf-8") as f:
            fc = json.load(f)
    except (OSError, ValueError):
        fc = {}

    try:
        locations = ac.active_locations(ac.DEFAULT_LOCATIONS)
    except Exception:
        locations = []

    results = []
    for loc in locations:
        name = loc["name"]
        entry = fc.get(name) or {}
        gws = entry.get("golden_windows") or []
        if not gws:
            continue
        gw = gws[0]
        try:
            night = _dt.fromisoformat(gw["night"])
            gws_dt = _dt.combine(night,
                _dt.strptime(gw["start"], "%H:%M").time())
            hours = max(1, gw.get("hours", 1))
        except (ValueError, KeyError):
            continue

        # Wenn Fenster vorbei -> skip
        if gws_dt < now - _td(hours=1):
            continue

        # Ruestzeit abziehen (Teleskop-Auskuehlung, Polausrichtung)
        arrival_deadline = gws_dt - _td(minutes=setup_minutes)

        # Transit-Schaetzung: Distanzbasiert (40 km/h Mischgeschwindigkeit
        # fuer OPNV; GTFS-Router ist fuer den konkreten Fall da)
        dist_km = _haversine_km(HQ_LAT, HQ_LON, loc["lat"], loc["lon"])
        transit_min = round(dist_km / 40 * 60)  # 40 km/h inkl. Umstiege
        latest_departure = arrival_deadline - _td(minutes=transit_min)

        # Erreichbarkeit
        time_to_departure = (latest_departure - now).total_seconds() / 60
        if time_to_departure > 15:
            status = "GO"
            status_detail = (f"Abfahrt in {time_to_departure / 60:.0f}h "
                             f"{time_to_departure % 60:.0f}m")
        elif time_to_departure > 0:
            status = "DEPARTURE_IMMINENT"
            status_detail = f"ABFAHRT IN {time_to_departure:.0f} MINUTEN!"
        else:
            status = "MISSED"
            status_detail = "Nicht mehr rechtzeitig erreichbar"

        results.append({
            "location": name,
            "elevation_m": loc.get("elevation_m", 100),
            "bortle": loc.get("bortle_class"),
            "window_start": gw["start"],
            "window_hours": hours,
            "night": gw["night"],
            "arrival_deadline": arrival_deadline.strftime("%H:%M"),
            "latest_departure": latest_departure.strftime("%H:%M"),
            "transit_minutes": transit_min,
            "distance_km": round(dist_km, 1),
            "time_to_departure_min": round(time_to_departure, 0),
            "status": status,
            "status_detail": status_detail,
        })

    results.sort(key=lambda r: r.get("time_to_departure_min", 9999))
    return {"results": results, "now": now.isoformat(timespec="seconds"),
            "setup_minutes": setup_minutes, "hq": "Ilvesheim"}


# --- Luecke 1: Hoehen-Differenzierung / Inversions-Erkennung ---
def _inversion_adjusted(spot: dict, clouds: float, tau: float,
                        wind: float) -> dict:
    """Erkennt wahrscheinliche Rheingraben-Inversion und adjustiert
    Wolkenwerte fuer hochgelegene Standorte.

    Logik: Bei grosser Bewolkung + geringem Taupunkt-Spread + schwachem
    Wind liegt vermutlich Bodennebel/Hochnebel in der Ebene. Standorte
    >200m Hoehe (z.B. Koenigsstuhl 550m) koennen UEBER dieser Schicht
    klar sein. Das DWD-Modell modelliert die Talsohle, nicht den Berg.

    Rueckgabe: {"inversion_likely": bool, "clouds_adjusted": float,
                "elevation_m": int, "adjustment_pp": float}
    """
    elevation = spot.get("elevation_m", 100)
    inversion_likely = (
        clouds is not None and clouds > 70
        and tau is not None and tau < 3.0
        and wind is not None and wind < 10
        and elevation > 200
    )
    if not inversion_likely:
        return {"inversion_likely": False,
                "clouds_adjusted": clouds,
                "elevation_m": elevation, "adjustment_pp": 0}

    # Adjustierung: Hoeher = mehr Klärung, max 60pp Reduktion
    # Formel: (Hoehe - 100m) / 1000m * 100pp, gekappt bei 60pp
    # 550m Koenigsstuhl: (550-100)/1000*100 = 45pp Reduktion
    # 150m Weinheim: (150-100)/1000*100 = 5pp (gering)
    adjustment = min(60.0, max(0.0, (elevation - 100) / 1000 * 100))
    adjusted = max(0, clouds - adjustment)
    return {"inversion_likely": True,
            "clouds_adjusted": round(adjusted, 1),
            "elevation_m": elevation,
            "adjustment_pp": round(adjustment, 1)}


# --- Push-Benachrichtigungen: anstehende PRIME WINDOWs als JSON ---
@app.get("/api/notifications/upcoming")
def api_notifications_upcoming(hours_ahead: int = 48):
    """PRIME-WINDOW-Ereignisse der naechsten N Stunden fuer die App-
    Benachrichtigung (Local Notifications). Nutzt dieselbe Logik wie
    check_prime_window_push(): Fenster mit ueberdurchschnittlichen
    Bedingungen. Die App pollt hier bei jedem Oeffnen/Refresh."""
    import datetime as _dt
    from datetime import timedelta as _td

    now = _dt.datetime.now()
    fc = {}
    try:
        with open(ac.FORECAST_PATH, "r", encoding="utf-8") as f:
            fc = json.load(f)
    except (OSError, ValueError):
        pass

    def _win_metrics(entry, gw):
        try:
            start_h = int(gw["start"].split(":")[0])
        except (KeyError, ValueError):
            return None
        cl, se, wi, ta, n = [], [], [], [], 0
        for h in (entry.get("series") or []):
            ts = (h.get("ts") or "")[:10]
            try:
                hh = int((h.get("ts") or "")[11:13])
            except ValueError:
                continue
            if ts != gw.get("night"):
                continue
            if not (start_h - 1 <= hh <= start_h + gw.get("hours", 1)):
                continue
            if h.get("clouds") is not None: cl.append(h["clouds"])
            if h.get("seeing") is not None: se.append(h["seeing"])
            if h.get("wind") is not None: wi.append(h["wind"])
            if h.get("tau") is not None: ta.append(h["tau"])
            n += 1
        if n == 0:
            return None
        return (sum(cl) / len(cl) if cl else None,
                sum(se) / len(se) if se else None,
                max(wi) if wi else None,
                min(ta) if ta else None)

    # Durchschnitt aller Fenster als Vergleichsmassstab
    all_c, all_s = [], []
    for entry in fc.values():
        for gw in (entry.get("golden_windows") or [])[:3]:
            m0 = _win_metrics(entry, gw)
            if m0 and m0[0] is not None: all_c.append(m0[0])
            if m0 and m0[1] is not None: all_s.append(m0[1])
    avg_c = sum(all_c) / len(all_c) if all_c else None
    avg_s = sum(all_s) / len(all_s) if all_s else None

    events = []
    for name, entry in fc.items():
        for gw in (entry.get("golden_windows") or []):
            try:
                night = _dt.datetime.fromisoformat(gw["night"])
                gws = _dt.datetime.combine(night.date(),
                    _dt.datetime.strptime(gw["start"], "%H:%M").time())
            except (ValueError, KeyError):
                continue
            gwe = gws + _td(hours=max(1, gw.get("hours", 1)))
            # Nur Fenster in den naechsten N Stunden
            hours_until = (gws - now).total_seconds() / 3600
            if not (0 <= hours_until <= hours_ahead):
                continue
            m0 = _win_metrics(entry, gw)
            if not m0:
                continue
            clouds, seeing, wind, tau = m0
            # Ueberdurchschnittlich?
            premium = False
            conditions = []
            if avg_c is not None and clouds is not None and clouds <= avg_c * 0.75:
                premium = True
                conditions.append(f"Wolken \u00d8 {clouds:.0f}% (Schnitt {avg_c:.0f}%)")
            if avg_s is not None and seeing is not None and seeing <= avg_s * 0.8:
                premium = True
                conditions.append(f"Seeing \u00d8 {seeing:.1f}\u2033")
            if not premium:
                continue
            events.append({
                "location": name,
                "night": gw["night"],
                "start": gw["start"],
                "end": f"{(gwe.hour):02d}:{(gwe.minute):02d}",
                "hours_until_start": round(hours_until, 1),
                "clouds_avg": round(clouds, 0) if clouds is not None else None,
                "seeing_avg": round(seeing, 1) if seeing is not None else None,
                "wind_max": round(wind, 0) if wind is not None else None,
                "conditions": conditions,
                "type": "PRIME_WINDOW",
            })
    events.sort(key=lambda e: e["hours_until_start"])
    return {"events": events, "generated_at": now.isoformat(timespec="seconds")}


class ObsModeBody(BaseModel):
    active: bool


@app.get("/api/observation-mode")
def api_observation_mode():
    try:
        state = ac.load_state()
    except (OSError, ValueError):
        state = {}
    return {"observation_mode": bool(state.get("observation_mode", False))}


@app.post("/api/observation-mode")
def api_observation_mode_set(body: ObsModeBody, request: Request):
    state = ac.load_state()
    state["observation_mode"] = bool(body.active)
    try:
        ac.save_state(state)
    except (OSError, ValueError) as e:
        raise HTTPException(503, f"State nicht speicherbar ({type(e).__name__})")
    log.info("[API] observation_mode -> %s", body.active)
    return {"observation_mode": body.active}


@app.get("/api/telegram-commands")
def api_telegram_commands():
    """Befehls-Referenz des Bots (read-only) - gespeist aus der gleichen
    Konstante, gegen die der Sync-Test die Handler prueft."""
    return {
        "bot_name": "@AstroCrawler007bot",
        "commands": getattr(ac, "TELEGRAM_COMMANDS", []),
    }


def _norm_key(s: str) -> str:
    """Namens-Normalisierung fuer robuste Lookups: Unicode-NFC (z.B.
    umlaut-formen), Whitespace-Folding, casefold, Underscore/Bindestrich
    wie Leerzeichen (slug-Formen wie 'ellerstadt_ost' treffen damit
    'Ellerstadt Ost'). Klammern/Kommata im Namen bleiben zulaessig,
    werden aber nicht mehr exakt benoetigt."""
    import unicodedata
    n = unicodedata.normalize("NFC", s or "").strip().casefold()
    n = n.replace("_", " ").replace("-", " ")
    return " ".join(n.split())


@app.get("/api/forecast")
def api_forecast(name: Optional[str] = None, id: Optional[str] = None):
    """Vorausschau eines Standorts bis Sonnenaufgang (vom letzten Heavy-
    Crawl). Lookup: ?id= hat Vorrang (stabile id aus locations.json),
    sonst ?name= - exakt, dann normalisiert (NFC/Whitespace/casefold)."""
    if id:
        try:
            locs = ac.active_locations(ac.DEFAULT_LOCATIONS) \
                + ac.load_watchlist()
        except Exception as e:
            log.warning("[API] Standort-Lookup fehlgeschlagen: %s", type(e).__name__)
            locs = []
        loc = next((l for l in locs if l.get("id") == id), None)
        if loc is None:
            raise HTTPException(404, f"Kein Standort mit id '{id}'")
        name = loc.get("name") or name
    if not name:
        raise HTTPException(400, "name= oder id= erforderlich")
    try:
        with open(ac.FORECAST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise HTTPException(503, "Vorausschau noch nicht aufgebaut "
                                 "(wartet auf den nächsten Heavy-Crawl)")
    if name not in data:
        norm = {_norm_key(k): k for k in data}
        hit = norm.get(_norm_key(name))
        if hit:
            return _forecast_payload(data[hit], hit)
        raise HTTPException(
            404, f"Keine Vorausschau für '{name}' "
                 f"(neuer Standort? Der nächste Heavy-Tick "
                 f"(30 min) liefert sie nach)")
    return _forecast_payload(data[name], name)


def _load_bias() -> dict:
    """Aktive Bias-Korrekturwerte (vom Crawler taeglich berechnet)."""
    try:
        with open(getattr(ac, "BIAS_PATH",
                          "/home/enigma/.astro_crawler_bias.json"),
                  "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# Fehleranalyse 2026-10-04: Die Verifikation ist zirkulaer (Prognose und
# "Realitaet" stammen beide aus DWD-Modellen). Ein negativer Bias (System
# "optimistisch") blaeht die Wolken-Anzeige auf (Korrektur = raw - bias =
# raw + |bias|). Ohne Deckel: 50% raw + 15,6 = 65,6% angezeigt.
BIAS_CAP_CLOUDS = 10.0   # pp — Deckel gegen zirkulaere Aufschaukelung
BIAS_CAP_SEEING = 0.30   # Bogner-Sekunden — gleiche Logik


def _apply_bias_to_series(series: list, bias: dict,
                          now) -> tuple[list, dict]:
    """Wendet die Bias-Korrektur NUR auf die Anzeigewerte einer Stunde an
    (clouds/seeing); ok/reasons/Golden-Window sind mit den Rohwerten
    bewertet und bleiben unveraendert - bewusste Vorgabe (Kalibrierung
    der Anzeige vor einer etwaigen spaeteren Bewertungskalierung).
    Rueckgabe: (series, angewandte-Korrekturen-Uebersicht).

    Fix 04.10.: Bias-Korrektur gedeckelt (BIAS_CAP_CLOUDS/SEEING) —
    die zirkulaere Verifikation (DWD-Modell gegen DWD-Modell) kann
    systematische Fehler verstaerken statt korrigieren, besonders im
    Herbst bei Inversionswetterlagen im Rheingraben."""
    import datetime as _dt
    applied = {}
    if not bias:
        return series, applied
    out = []
    for h in series:
        h = dict(h)
        ts = h.get("ts")
        try:
            lead_h = (_dt.datetime.fromisoformat(ts) - now
                      ).total_seconds() / 3600
        except (ValueError, TypeError):
            lead_h = None
        bucket = "le24" if (lead_h is not None and lead_h <= 24) else "gt24"
        for param, lo, hi, cap in (("clouds", 0, 100, BIAS_CAP_CLOUDS),
                                   ("seeing", 0.05, 20, BIAS_CAP_SEEING)):
            b = ((bias.get(param) or {}).get(bucket) or {}).get("bias")
            if b is None or h.get(param) is None:
                continue
            # Deckel: verhindert dass zirkulaere Bias-Werte die Anzeige
            # massiv verschieben (Feld-Report: 50% raw -> 65% angezeigt)
            b_capped = max(-cap, min(cap, b))
            h[f"{param}_raw"] = h[param]
            h[f"{param}_bias_applied"] = b_capped
            # err = vorhergesagt - Ist; positives err = Uberschaetzung
            # -> Korrektur vom Prognosewert ABZIEHEN, dann klemmen
            h[param] = round(min(hi, max(lo, h[param] - b_capped)), 1)
            applied[f"{param}_{bucket}"] = {"bias": b_capped,
                "bias_raw": b, "n":
                (bias.get(param) or {}).get(bucket, {}).get("n")}
        out.append(h)
    return out, applied


def _forecast_payload(entry: dict, key: str) -> dict:
    """Responsse-Ansicht: verstrichene Stunden abschneiden (aeltester
    Eintrag = aktuelle Stunde) und Verfuegbarkeit gegen den 48-h-Ziel-
    horizont kennzeichnen. Die gespeicherten Daten bleiben unveraendert."""
    import datetime as _dt
    out = dict(entry)
    now = _dt.datetime.now()
    cutoff = now.strftime("%Y-%m-%dT%H:00")
    series = out.get("series") or []
    trimmed = [h for h in series if (h.get("ts") or "") >= cutoff]
    bias = _load_bias()
    trimmed, bias_applied = _apply_bias_to_series(trimmed, bias, now)
    out["series"] = trimmed
    if bias_applied:
        out["bias_applied"] = bias_applied
    last = max((h.get("ts") or "") for h in trimmed) if trimmed else None
    avail_h = 0.0
    if last:
        try:
            avail_h = ((_dt.datetime.fromisoformat(last)
                        + _dt.timedelta(hours=1)) - now
                       ).total_seconds() / 3600
        except (ValueError, TypeError):
            avail_h = 0.0
    horizon = getattr(ac, "FORECAST_HORIZON_HOURS", 48)
    out["forecast_hours_remaining"] = round(avail_h, 1)
    out["incomplete"] = bool(last is None or avail_h < horizon - 1)
    if out["incomplete"]:
        out["note"] = (f"Vorausschau unvollständig: nur {avail_h:.0f} h ab "
                       f"jetzt verfügbar (Ziel ≥ {horizon} h) - der nächste "
                       f"Heavy-Tick (30 min) ergänzt.")
    return out


# --- FWHM-Sync: Nachtrag-Endpunkt nach Sessionende (kein Live-Anspruch) ---
class FwhmBody(BaseModel):
    measurements: list[dict]
    location: Optional[str] = None
    source: Optional[str] = None


@app.post("/api/fwhm_sync")
def api_fwhm_sync(body: FwhmBody, request: Request):
    """JSON-Array von Messungen entgegennehmen und in fwhm_log schreiben.
    Zeilen-Format: {"ts": ISO, "fwhm": 2.4, "location"?: "...", "source"?:"..."}.
    Ungueltige Zeilen werden uebersprungen und gezaehlt, nichts wirft ab."""
    if API_TOKEN and request.headers.get("x-api-token") != API_TOKEN:
        raise HTTPException(401, "Ungueltiger API-Token")
    if not body.measurements:
        raise HTTPException(400, "measurements[] ist leer")
    if len(body.measurements) > 5000:
        raise HTTPException(413, "max. 5000 Messungen pro Sync")
    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    rows, skipped = [], 0
    for m in body.measurements:
        try:
            ts = str(m["ts"])
            dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))  # validieren
            fwhm = float(m["fwhm"])
            if not 0.05 < fwhm < 20:
                raise ValueError("fwhm ausserhalb 0.05-20\"")
            rows.append((ts, fwhm,
                         m.get("location") or body.location,
                         m.get("source") or body.source, now_iso))
        except Exception as e:
            log.warning("[API] fwhm-Messung uebersprungen (%s): %s", m.get("ts"), type(e).__name__)
            skipped += 1
    if not rows:
        raise HTTPException(400, "keine gueltige Messung dabei "
                                 "(Format: {ts: ISO, fwhm: float})")
    conn = _db()
    try:
        conn.executemany(
            "INSERT INTO fwhm_log (ts, fwhm_arcsec, location_name, source, "
            "created_at) VALUES (?,?,?,?,?)", rows)
        conn.commit()
    finally:
        conn.close()
    log.info("[API] FWHM-Sync: %d eingefuegt, %d uebersprungen", len(rows), skipped)
    return {"ok": True, "inserted": len(rows), "skipped": skipped}


# --- Bortle/Lichtverschmutzung am Standort (Pixel-Sampling aus LP-Tiles) ---
from lpcache import bortle_at  # noqa: E402

_BORTLE_CACHE: dict = {}  # (lat,lon) -> Ergebnis; LP aendert sich jaehrlich


@app.get("/api/bortle")
def api_bortle(lat: float, lon: float):
    """Zenit-Lichtverschmutzung (Lorenz-Zone, mag/arcsec^2, Bortle-Naeherung)."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(400, "Koordinaten ausserhalb des Bereichs")
    key = (round(lat, 3), round(lon, 3))
    if key not in _BORTLE_CACHE:
        _BORTLE_CACHE[key] = bortle_at(lat, lon)
    return _BORTLE_CACHE[key]


# --- Changelog: append-only Einträge, neueste zuerst ---
@app.get("/api/changelog")
def api_changelog():
    try:
        with open(os.path.join(FRONTEND_DIR, "changelog.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
        return {"entries": list(reversed(data.get("entries", [])))}
    except (OSError, ValueError):
        raise HTTPException(503, "changelog.json nicht lesbar")


# --- Uptime-Monitoring (healthchecks.io): in-process Dead-Man's-Switch ---
# Bewusst IM uvicorn-Prozess: Prozess tot => Pings stoppen => healthchecks
# alarmiert nach der Grace Time. Ein externer Ping-Prozess wuerde dagegen
# weiterpingen und den Ausfall nie melden. Leer = inaktiv.
import urllib.request as _urlreq  # noqa: E402

PING_URL_APP = os.environ.get("HEALTHCHECK_PING_URL_APP", "")


@app.on_event("startup")
async def _healthcheck_loop():
    if not PING_URL_APP:
        log.info("[Healthcheck] App-Ping deaktiviert (keine URL gesetzt)")
        return

    async def _loop():
        import asyncio as _aio

        def _ping():
            _urlreq.urlopen(
                _urlreq.Request(PING_URL_APP,
                                headers={"User-Agent": "astro-cortex"}),
                timeout=10).read()

        while True:
            try:
                await _aio.to_thread(_ping)
                log.info("[Healthcheck] app-Ping OK")
            except Exception as e:
                log.warning("[Healthcheck] app-Ping fehlgeschlagen: %s",
                            type(e).__name__)
            await _aio.sleep(900)

    import asyncio as _aio2
    _aio2.get_running_loop().create_task(_loop())


# --- Cache-Header: Shell-Dateien immer revalidieren (Fix 15.08.) ---
# Der Service Worker selbst (sw.js) und die Shell-Dateien duerfen niemals aus
# Browser-/Proxy-Caches kommen, sonst erreicht ein Deploy die installierte PWA
# nicht. 'no-cache' = Revalidation mit ETag (StaticFiles liefert ETag/Last-
# Modified mit) -> effizient UND immer frisch. Tiles/Icons duerfen lange
# gecacht werden (aendern sich nie).
# CORS: Die native App (Capacitor, Origin https://localhost) ruft den
# Cloudflare-Tunnel https://api.teamigel.com cross-origin auf - mit
# CREDENTIALS (CF_Authorization-Cookie). Wildcard + Credentials ist per
# Spec verboten, deshalb explizite Origins; Tailscale bleibt als Fallback.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://localhost", "capacitor://localhost",
                   "https://api.teamigel.com",
                   "https://seriousjoke.tailcc473e.ts.net"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "x-api-token",
                   "cf-access-client-id", "cf-access-client-secret"],
)


@app.middleware("http")
async def cache_control_headers(request, call_next):
    resp = await call_next(request)
    path = request.url.path
    if (path in ("/", "/index.html") or path.endswith((".html", ".js", ".css",
                                                      ".webmanifest", ".svg"))):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# Statisches Frontend (PWA) - zuletzt gemountet, damit /api/* Vorrang hat
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
