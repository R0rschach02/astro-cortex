#!/usr/bin/env python3
"""Astro Command Center - FastAPI-Backend.

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
from fastapi.responses import FileResponse, Response  # noqa: E402
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
    except Exception:
        return None


def _spot_state(loc: dict, profile: str = "dso") -> dict:
    """Kombinierte Sicht pro Standort:
    - Seeing/Wolken/Rating aus dem juengsten HEAVY-Crawl (30-Min-Takt)
    - Radar/Wind/Taupunkt + Nachtverlauf aus dem juengsten Lauf (5-Min-Takt)
    - Mond/Dunkelheit/Planeten aus dem Tages-Cache (skyfield)
    Rating wird mit dem aktiven Beobachtungsprofil (dso|planet) berechnet.
    """
    out = {"name": loc["name"], "lat": loc["lat"], "lon": loc["lon"],
           "is_live": loc["name"].startswith("Live "), "age_min": None}
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
async def api_watch(body: WatchBody, request: Request):
    """Live-Standort auf die Watchlist (Pendant zu /watch per Telegram).
    Nutzt denselben fcntl-Lock wie der Bot - keine lost updates."""
    if API_TOKEN and request.headers.get("x-api-token") != API_TOKEN:
        raise HTTPException(401, "Ungueltiger API-Token")
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


# --- Vorausschau: stündliche Reihe + Golden Window (latest-wins JSON) ---
@app.get("/api/forecast")
def api_forecast(name: str):
    """Vorausschau eines Standorts bis Sonnenaufgang (vom letzten Heavy-Crawl)."""
    try:
        with open(ac.FORECAST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        raise HTTPException(503, "Vorausschau noch nicht aufgebaut "
                                 "(wartet auf den nächsten Heavy-Crawl)")
    if name not in data:
        raise HTTPException(404, f"Keine Vorausschau für '{name}'")
    return data[name]


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
        except Exception:
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
    except Exception:
        raise HTTPException(503, "changelog.json nicht lesbar")


# --- Cache-Header: Shell-Dateien immer revalidieren (Fix 15.08.) ---
# Der Service Worker selbst (sw.js) und die Shell-Dateien duerfen niemals aus
# Browser-/Proxy-Caches kommen, sonst erreicht ein Deploy die installierte PWA
# nicht. 'no-cache' = Revalidation mit ETag (StaticFiles liefert ETag/Last-
# Modified mit) -> effizient UND immer frisch. Tiles/Icons duerfen lange
# gecacht werden (aendern sich nie).
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
