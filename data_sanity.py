"""data_sanity: deterministische Plausibilitaetsschicht fuer alle Datenquellen.

Kein ML, kein neues Logging-System - drei Pruefklassen, jede liefert
Meldungen via logging.warning (Praefix [sanity]), sichtbar im Journal,
kein Telegram. Jede Meldung maximal 1x/Tag (eigenes Mini-State-File).

Klasse 1  Wertebereiche     - Parameter gegen harte Grenzen
Klasse 2  Totlauf-Erkennung - eingefrorene Serien, identische Vorhersage-
                              Reihen, Fehlerquote der Heavy-Laeufe
Klasse 3  Zwei-Quellen-Abgleich - gleiche Groesse aus zwei Quellen stark
                              unterschiedlich: loggen statt stillschweigend
                              eine nehmen (Kaskade greift nur bei Ausfall)

Hintergrund: ClearOutside crashte 36 h unbemerkt in jedem Heavy-Lauf
(UnboundLocalError, alles lief auf OM-Fallback). Klasse 2c haette das
innerhalb eines Tages als WARNING gemeldet.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta

log = logging.getLogger("data_sanity")

STATE_PATH = os.path.expanduser("~/.astro_crawler_sanity.json")

# Klasse 1: (min, max) - None = unbesetzt. Felder = SiteReport-Attribute.
VALUE_RANGES = {
    "clouds_total": (0, 100),      # Prozent
    "clouds_low": (0, 100),
    "clouds_mid": (0, 100),
    "clouds_high": (0, 100),
    "rain_prob": (0, 100),
    "seeing": (0.05, 20.0),        # Bogensekunden (wie FWHM-Standard)
    "jetstream": (0, 200),         # m/s
    "wind_speed": (0, 250),        # km/h
    "wind_gusts": (0, 300),        # km/h
    "precip_2h": (0, 100),         # mm in 2 h; >100 mm ist Messfehler
    "dewpoint_spread": (-5, 40),   # K
    "moon_illum": (0, 100),
    "moon_max_alt": (-90, 90),
}

# Klasse 3: |CO - OM| in Prozentpunkten, ab der geloggt wird
CROSS_SOURCE_CLOUD_PP = 40
# Klasse 2c: Fehlerquote der Heavy-Laeufe in 24 h, ab der gewarnt wird
ERROR_RATIO_LIMIT = 0.30
# Klasse 2a/b: so viele identische Werte in Folge = Verdacht Totlauf
STALE_CRAWL_RUNS = 6
STALE_FORECAST_RUN = 12


def _suppressed_today(key: str) -> bool:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    return st.get(key) == f"{datetime.now():%Y-%m-%d}"


def _mark_reported(key: str):
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    st[key] = f"{datetime.now():%Y-%m-%d}"
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


def _warn(key: str, msg: str):
    """1x/Tag pro Pruefschluessel; der Text darf sich aendern."""
    if _suppressed_today(key):
        return
    _mark_reported(key)
    log.warning("[sanity] %s", msg)


# ---------------------------------------------------------------------------
# Klasse 1: Wertebereiche
# ---------------------------------------------------------------------------
def check_value_ranges(reports: list) -> list:
    findings = []
    for rep in reports:
        for field, (lo, hi) in VALUE_RANGES.items():
            v = getattr(rep, field, None)
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if v < lo or v > hi:
                findings.append(f"{rep.name}: {field}={v:g} ausserhalb "
                                f"[{lo:g}, {hi:g}]")
    if findings:
        _warn("ranges", "Wertebereich-Verstoesse: " + "; ".join(findings[:5])
              + (" ..." if len(findings) > 5 else ""))
    return findings


# ---------------------------------------------------------------------------
# Klasse 2: Totlauf-Erkennung (DB-basiert)
# ---------------------------------------------------------------------------
def check_stale_crawls(conn) -> list:
    """Letzte STALE_CRAWL_RUNS Heavy-Zeilen je Standort: identischer
    clouds_total-Wert = Verdacht auf eingefrorene Quelle (Quelle liefert
    nur noch denselben Wert oder Fehler-Broadcast)."""
    findings = []
    rows = conn.execute(
        "SELECT location_name, clouds_total, clouds_source FROM ("
        " SELECT location_name, clouds_total, clouds_source,"
        "        ROW_NUMBER() OVER (PARTITION BY location_name"
        "            ORDER BY ts DESC) rn"
        " FROM crawls WHERE mode='heavy' AND clouds_total IS NOT NULL)"
        "WHERE rn <= ? ORDER BY location_name, rn", (STALE_CRAWL_RUNS,)
    ).fetchall()
    per_loc = {}
    for loc, val, src in rows:
        per_loc.setdefault(loc, []).append((val, src))
    for loc, series in per_loc.items():
        if len(series) >= STALE_CRAWL_RUNS and \
                len({v for v, _ in series}) == 1:
            v, src = series[0]
            findings.append(f"{loc}: clouds_total={v:g} in den letzten "
                            f"{len(series)} Heavy-Laeufen identisch "
                            f"(Quelle {src})")
    if findings:
        _warn("stale_crawls", "Eingefrorene Messserien: " + "; ".join(findings))
    return findings


def check_stale_forecast_series(conn) -> list:
    """Juengste forecast_log-Reihe je Standort: viele identische Wolken-
    Werte in Folge deuten auf geparste Fehler-/Platzhalterseite."""
    findings = []
    locs = [r[0] for r in conn.execute(
        "SELECT DISTINCT location_name FROM forecast_log")]
    for loc in locs:
        vals = [r[0] for r in conn.execute(
            "SELECT clouds_total FROM forecast_log WHERE location_name=? "
            "AND clouds_total IS NOT NULL ORDER BY id DESC LIMIT ?",
            (loc, STALE_FORECAST_RUN))]
        if len(vals) >= STALE_FORECAST_RUN and len(set(vals)) == 1:
            findings.append(f"{loc}: {len(vals)} Vorhersagezeilen in Folge "
                            f"alle clouds_total={vals[0]:g}")
    if findings:
        _warn("stale_forecast", "Monotone Vorhersagereihen: "
              + "; ".join(findings))
    return findings


def check_error_ratio(conn) -> list:
    """Fehlerquote der Heavy-Laeufe der letzten 24 h. Haette den 36-h-
    ClearOutside-Tod (jeder Lauf UnboundLocalError) am ersten Tag gemeldet."""
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat(timespec="minutes")
    row = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN errors IS NOT NULL AND errors != '' "
        "THEN 1 ELSE 0 END) FROM crawls WHERE mode='heavy' AND ts >= ?",
        (cutoff,)).fetchone()
    total, bad = row[0] or 0, row[1] or 0
    if total >= 10 and bad / total > ERROR_RATIO_LIMIT:
        msg = (f"{bad}/{total} Heavy-Laeufe der letzten 24 h mit Quellen-"
               f"Fehler (>{ERROR_RATIO_LIMIT:.0%}) - Primaeerquelle pruefen!")
        _warn("error_ratio", msg)
        return [msg]
    return []


# ---------------------------------------------------------------------------
# Klasse 3: Zwei-Quellen-Abgleich
# ---------------------------------------------------------------------------
def check_cross_source_clouds(conn) -> list:
    """ClearOutside- vs. Open-Meteo-Wolkenvorhersage fuer dieselbe Stunde:
    grosse Abweichung loggen (beide verfuegbar, Kaskade greift nicht).
    Basis: je (Standort, Stunde, Quelle) nur die JUENGSTE forecast_log-Zeile
    der letzten 24 h - forecast_log appendet alle 30 min neue Generationen,
    ein simpler JOIN wuerde sonst jede Stunde hundertfach zaehlen."""
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat(timespec="minutes")
    rows = conn.execute(
        "WITH co AS (SELECT location_name, target_ts, clouds_total, MAX(id)"
        "  FROM forecast_log WHERE source_clouds='clearoutside'"
        "  AND clouds_total IS NOT NULL AND created_at >= ?"
        "  GROUP BY location_name, target_ts),"
        " om AS (SELECT location_name, target_ts, clouds_total, MAX(id)"
        "  FROM forecast_log WHERE source_clouds='open_meteo'"
        "  AND clouds_total IS NOT NULL AND created_at >= ?"
        "  GROUP BY location_name, target_ts)"
        "SELECT co.location_name, co.target_ts, co.clouds_total, om.clouds_total "
        "FROM co JOIN om ON om.location_name = co.location_name "
        " AND om.target_ts = co.target_ts "
        "WHERE ABS(co.clouds_total - om.clouds_total) >= ?",
        (cutoff, cutoff, CROSS_SOURCE_CLOUD_PP)).fetchall()
    if rows:
        ex = rows[-1]
        _warn("cross_clouds",
              f"Wolken-Prognosen weichen ab (CO vs OM, >= {CROSS_SOURCE_CLOUD_PP} pp): "
              f"{len(rows)} (Standort, Stunde)-Paare betroffen; z.B. "
              f"{ex[0]} {ex[1]}: CO {ex[2]:.0f}% vs OM {ex[3]:.0f}%")
    return rows


# ---------------------------------------------------------------------------
# Oeffentliche API
# ---------------------------------------------------------------------------
def run_sanity(reports: list, db_path: str) -> dict:
    """Alle Pruefungen laufen; Ergebnis dict fuer Tests/Log-Auswertung.
    Wirft nie (Sanity-Check darf den Crawler nicht killen)."""
    out = {"ranges": [], "stale_crawls": [], "stale_forecast": [],
           "error_ratio": [], "cross_clouds": []}
    try:
        out["ranges"] = check_value_ranges(reports)
    except Exception as e:
        log.warning("[sanity] ranges fehlgeschlagen: %s", type(e).__name__)
    try:
        conn = sqlite3.connect(db_path)
        out["stale_crawls"] = check_stale_crawls(conn)
        out["stale_forecast"] = check_stale_forecast_series(conn)
        out["error_ratio"] = check_error_ratio(conn)
        out["cross_clouds"] = check_cross_source_clouds(conn)
        conn.close()
    except Exception as e:
        log.warning("[sanity] DB-Pruefungen fehlgeschlagen: %s",
                    type(e).__name__)
    n = sum(len(v) for v in out.values())
    if n == 0:
        log.info("[sanity] alle Pruefungen unauffaellig")
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.expanduser("~/.astro_crawler.db")
    res = run_sanity([], db)
    print(f"\nZusammenfassung: {sum(len(v) for v in res.values())} Auffaelligkeiten")
