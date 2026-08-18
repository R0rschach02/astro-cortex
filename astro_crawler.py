#!/usr/bin/env python3
"""
Astro-Crawler: Go/No-Go Entscheidungshilfe für Teleskop-Einsätze.

Datenquellen:
  1. ClearOutside.com      -> Totale Bewölkung, Low/Mid/High Clouds, Regen (nächste 4 h)
  2. Meteoblue.com         -> Astronomical Seeing ("), Jetstream (m/s), Seeing-Index (1-5)
  3. Kachelmannwetter.com  -> Niederschlag/Gewitter-Erkennung (nächste 2 h)
     Fallback: Bright Sky API (offizielle DWD-Daten) für Warnungen + Radar-Proxy

Technik:
  - Playwright (Chromium) mit Stealth-Maßnahmen gegen Cloudflare/Bot-Checks
  - Dynamisches Warten auf relevante Elemente (kein blinder sleep)
  - Fallback-Loop: CSS-Selektor -> XPath -> Regex im gerenderten HTML
  - Detailliertes Logging welches Selektor gerade probiert wird (Feedback-Loop)

Installation:
  pip install playwright
  playwright install chromium

Aufruf-Beispiele:
  python astro_crawler.py                          # alle 3 Default-Locations (Heavy)
  python astro_crawler.py --radar-only             # DWD/BrightSky + Bot-Befehle
  python astro_crawler.py --lat 50.0000008 --lon 8.0000008 --name "Neckarplatten"
  python astro_crawler.py --watch --interval 30    # Dauerbetrieb alle 30 min
  python astro_crawler.py --no-headless --debug

Telegram-Bot @AstroCrawler007bot (Alarme + Steuerung):
  /status [lat lon]   Voller Lagecheck (Playwright), ohne Args: Spots-Kurzinfo
  /spots              Radar-Schnellcheck der 3 Spots + letzte Ratings + Mond
  /watch lat lon [h]  Live-Standort fuer N Stunden (Default 2) in den
                      5-Min-Radar-Timer aufnehmen -> automatische Alarme
  /unwatch            Alle Live-Standorte entfernen
  /rate W S T         Session-Feedback 1-5 je Wolken/Seeing/Transparenz,
                      z.B. "/rate 4 3 5" -> Wolken=4, Seeing=3, Transparenz=5
                      (bezieht sich auf die zuletzt aktive Location)
  /help               Diese Hilfe

Persistenz:
  ~/.astro_crawler_state.json    Alert-State (Radar-Status, Ratings, Cooldowns)
  ~/.astro_crawler_watchlist.json Live-Standorte mit Ablaufzeit
  ~/.astro_crawler.db            SQLite-Historie: Rohwerte + Mond + Feedback
  ~/.astro_crawler_moon.json     Tages-Cache der Mond-Ephemeriden pro Standort
  ~/.skyfield/de421.bsp          JPL-Ephemeris (einmalig 17 MB, dann lokal)

Split-Timing fuer 24/7-Betrieb (siehe ~/.config/systemd/user/):
  astro-crawler.timer  -> astro-crawler.service  (Heavy: Playwright, alle 30 min)
  astro-radar.timer    -> astro-radar.service    (Radar+Bot: DWD/skyfield, 5 min)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import re
import sqlite3
import sys
import time
import traceback
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

DEFAULT_LOCATIONS = [
    {"name": "Mannheim Neckarplatten", "lat": 50.0000001, "lon": 8.0000001},
    # Koordinaten-Update 2026-08-16 (Marcel, vor Ort): alter Wert 50.0000006/8.0000006
    {"name": "Hemsbach (Sulzbach)",   "lat": 50.0000002, "lon": 8.0000002},
    # Koordinaten-Update 2026-08-16 (Marcel, vor Ort): alter Wert 50.0000007/8.0000007
    # Name bewusst UNveraendert, damit die DB-Historie laeuft.
    {"name": "Weinheim",              "lat": 50.0000003,  "lon": 8.0000003},
    {"name": "Spinelli Park",         "lat": 50.0000004, "lon": 8.0000004},
    # Neu 2026-08-16: Feld an der L631
    {"name": "Viernheim (Heddesheimer Str.)", "lat": 50.0000005, "lon": 8.0000005},
]

# Bot: @AstroCrawler007bot - Zugangsdaten liegen in ~/.env (NICHT im Repo,
# siehe .env.example). _load_env() ist eine Mini-.env-Loader ohne Fremd-
# paket, damit oneshot-Timer nie an einer fehlenden Abhaengigkeit haengen.
def _load_env(path: str = None):
    path = path or os.path.expanduser("~/.env")
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"'))
    except FileNotFoundError:
        pass


_load_env()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEMETRY_DISABLED = False                 # --no-telegram setzt das auf True

# Optionaler Schutz schreibender API-Endpunkte (Backend liest dieselbe .env,
# da main.py astro_crawler importiert - _load_env laeuft dabei mit):
#   ASTRO_API_TOKEN=geheim

# Python-3.10-Kompatibilität: Backslash gehört nicht in den f-string-Ausdruck,
# daher wird das Bogensekunden-Zeichen als Konstante übergeben.
ARCSEC = '"'

HEADLESS = True          # --no-headless überschreibt das
NAV_TIMEOUT_MS = 30_000  # Maximale Wartezeit pro Seitenaufbau
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

log = logging.getLogger("astro")


@dataclass
class SiteReport:
    """Ergebnis-Container einer Location."""
    name: str
    lat: Optional[float] = None          # fuer DB/Mond-Nachberechnung
    lon: Optional[float] = None
    clouds_total: Optional[int] = None   # %
    clouds_low: Optional[int] = None     # %
    clouds_mid: Optional[int] = None     # %
    clouds_high: Optional[int] = None    # %
    clouds_source: Optional[str] = None  # "clearoutside" | "brightsky_fallback"
    rain_prob: Optional[int] = None      # % (nächste 4 h, Maximum)
    seeing: Optional[float] = None       # Bogensekunden
    jetstream: Optional[float] = None    # m/s
    seeing_index: Optional[int] = None   # 1 (schlecht) .. 5 (exzellent)
    seeing_source: Optional[str] = None  # "meteoblue"
    radar_status: str = "Unknown"        # "Clear" | "Rain Alert" | "Storm Alert" | "Unknown"
    precip_2h: Optional[float] = None    # mm (BrightSky/DWD)
    clouds_2h: Optional[list] = None     # % je Stunde, naechste 2 h (transient,
                                         # nur fuer /clear-Pruefung, nicht in DB)
    gusts_2h: Optional[float] = None     # km/h, Boeen max 2h, FRISCH je Radar-
                                         # Tick (Basis fuer Wind-Debounce)
    wind_speed: Optional[float] = None   # km/h, Max. 2 h (BrightSky/DWD)
    dewpoint_spread: Optional[float] = None  # K, Min. 2 h: Temp - Taupunkt
    # Nachtverlauf aus BrightSky (TTL-gecacht, siehe check_brightsky_night)
    night_temp_min: Optional[float] = None   # °C bis Sonnenaufgang
    night_temp_max: Optional[float] = None   # °C (relevant: ungekuehlte 600D)
    night_rh_max: Optional[int] = None       # % rel. Luftfeuchte max
    night_cloud_min: Optional[int] = None    # % klarste Stunde der Nacht
    wind_gusts: Optional[float] = None       # km/h, Boeen max der Nacht (EQ5!)
    # Beschlags-Score (offener 150P-Newton OHNE Tauheizung)
    dew_risk: Optional[str] = None       # "hoch" | "mittel" | "gering"
    # Astronomie (skyfield, lokal): Mond + Dunkelheit + Planeten
    moon_illum: Optional[float] = None   # %
    moon_max_alt: Optional[float] = None # Grad
    moon_culm: Optional[str] = None      # HH:MM Kulmination (lokale Zeit)
    moon_window: Optional[str] = None    # "HH:MM-HH:MM" Hoehe > 30 Grad
    moon_rise: Optional[str] = None      # HH:MM oder None
    moon_set: Optional[str] = None       # HH:MM oder None
    dark_window: Optional[str] = None    # "HH:MM-HH:MM" Sonne < -18 Grad
    planets: Optional[dict] = None       # {"jupiter": {max_alt, culm, window}, ...}
    # Vorausschau-Reihen (transient, NICHT in der DB - latest-wins als JSON):
    # gefuellt von den Quellen-Scrapern, kombiniert von build_forecast()
    fc_clouds: Optional[list] = None     # [{ts, total, low, mid, high, rain}] (Nacht-1-Quelle: CO)
    fc_clouds_om: Optional[list] = None  # gleiche Form, IMMER aus Open-Meteo (72 h;
                                         # deckt Nacht 2+3, ClearOutside liefert nur ~24 h)
    fc_seeing: Optional[list] = None     # [{ts, seeing, idx, jet}] alle Meteoblue-Tage
    fc_ground: Optional[list] = None     # [{ts, cloud, precip, prob, wind, tau}]
    dark_windows: Optional[list] = None  # ["HH:MM-HH:MM"] je Nacht (bis zu 3)
    errors: list = field(default_factory=list)

    def compute_dew_risk(self):
        """Beschlags-Score fuer den offenen 150P-Newton OHNE Tauheizung.

        Physik: Der Fangspiegel strahlt nach KLAREM Himmel in den Weltraum ab
        (radiative cooling) und faellt 2-6 K unter die Lufttemperatur -
        Beschlag entsteht deshalb BEREITS bei Spread < 4-6 K, solange es klar
        und schwachwindig ist (Wind wuerde konvektiv nachheizen).

        hoch   = klar (<30% Wolken) + Schwachwind (<10 km/h) + Spread < 4 K
        mittel = klar + Spread < 6 K (Vorlauf-Warnung)
        gering = sonst
        """
        clouds = self.night_cloud_min if self.night_cloud_min is not None \
            else self.clouds_total
        spread = self.dewpoint_spread
        wind = self.wind_speed
        if clouds is None or spread is None or wind is None:
            self.dew_risk = None
            return self.dew_risk
        clear = clouds < 30
        calm = wind < 10
        if clear and calm and spread < 4:
            self.dew_risk = "hoch"
        elif clear and spread < 6:
            self.dew_risk = "mittel"
        else:
            self.dew_risk = "gering"
        return self.dew_risk

    def rate(self, profile: str = "dso") -> tuple[str, str]:
        """Go/No-Go nach Beobachtungsprofil.

        DSO (Triband, Default):
          Radar-Alarm > Wolken >40% > Seeing >3.0" (lockert von 2.0" -
          Flaeche/DSO ist seeing-toleranter) > Beschlag 'hoch' (Fangspiegel
          ohne Heizung!) > Mond >60% => MAYBE.
        PLANETARISCH (ASI662MC + SharpCap):
          Radar-Alarm > Seeing >2.0" hart (150mm ~1" Beugungsgrenze) >
          Jetstream >30 m/s > Wolken >50% > kein Planet >30 Grad in der Nacht.
        Gemeinsam: NO DATA, wenn weder Wolken noch Radar vorliegen.
        """
        rs = self.radar_status or "Unknown"
        if "Storm" in rs or "Rain" in rs:
            return "NO-GO", "🔴"

        if profile == "planet":
            if self.seeing is not None and self.seeing > 2.0:
                return "NO-GO", "🔴"
            if self.jetstream is not None and self.jetstream > 30:
                return "NO-GO", "🔴"
            if self.clouds_total is not None and self.clouds_total > 50:
                return "NO-GO", "🔴"
            # Bedingung: mindestens ein Planet (Jupiter/Saturn/Mars) > 30 Grad
            if self.planets and not any(
                    (p or {}).get("window") for p in self.planets.values()):
                return "NO-GO", "🔴"
            if rs == "Unknown" and self.clouds_total is None:
                return "NO DATA", "⚪"
            return "GO", "🟢"

        # --- DSO ---
        if self.clouds_total is not None and self.clouds_total > 40:
            return "NO-GO", "🔴"
        if self.seeing is not None and self.seeing > 3.0:
            return "NO-GO", "🔴"
        if self.dew_risk == "hoch":
            return "NO-GO", "🔴"
        if rs == "Unknown" and self.clouds_total is None:
            return "NO DATA", "⚪"
        if self.clouds_total is not None and self.clouds_total > 20:
            return "MAYBE", "🟡"
        if self.moon_illum is not None and self.moon_illum > 60:
            return "MAYBE", "🟡"
        return "GO", "🟢"


# ---------------------------------------------------------------------------
# Playwright-Setup
# ---------------------------------------------------------------------------

async def make_browser(headless: bool):
    from playwright.async_api import async_playwright

    log.info("Starte Chromium (headless=%s)", headless)
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=headless,
        args=[
            # Klassische Stealth-Flags: WebDriver-Spuren & Automation-Hinweise unterdrücken
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    context = await browser.new_context(
        user_agent=USER_AGENT,
        locale="de-DE",
        timezone_id="Europe/Berlin",
        viewport={"width": 1366, "height": 900},
    )
    # navigator.webdriver = False injizieren (Cloudflare prüft das)
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return pw, browser, context


async def log_page_state(page, source: str):
    """Debug-Hilfe für den Feedback-Loop: Titel + Cloudflare-Check ins Log."""
    try:
        title = await page.title()
        log.debug("[%s] Seitentitel: %r", source, title)
        content = await page.content()
        if "Just a moment" in content or "challenge" in content.lower()[:2000]:
            log.warning("[%s] Cloudflare-Challenge erkannt! (Titel: %r)", source, title)
    except Exception:
        pass


async def safe_text(source: str, strat: str, page, selector: str, timeout: int = 8000) -> Optional[str]:
    """Intelligentes Warten auf einen Selektor, mit Logging des Versuchs."""
    log.info("[%s] %s -> warte auf Selektor: %s", source, strat, selector)
    try:
        el = await page.wait_for_selector(selector, timeout=timeout, state="attached")
        if el is None:
            log.warning("[%s] %s -> Selektor nie erschienen: %s", source, strat, selector)
            return None
        txt = (await el.text_content() or "").strip()
        log.info("[%s] %s -> Treffer: %r", source, strat, txt[:80])
        return txt
    except Exception as e:
        log.warning("[%s] %s -> Selektor FEHLGESCHLAGEN (%s): %s", source, strat, type(e).__name__, selector)
        log.debug("[%s] Traceback:\n%s", source, traceback.format_exc())
        return None


async def regex_from_body(source: str, strat: str, page, pattern: str) -> Optional[str]:
    """Fallback: Regex-Suche im gerenderten Seitenquelltext (nach JS-Ausführung)."""
    log.info("[%s] %s -> Regex über Seitenquelltext: %s", source, strat, pattern)
    try:
        html = await page.content()
        m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if not m:
            log.warning("[%s] %s -> Regex ohne Treffer", source, strat)
            if log.isEnabledFor(logging.DEBUG):
                log.debug("[%s] HTML-Anfang (2000 Zeichen):\n%s", source, html[:2000])
            return None
        log.info("[%s] %s -> Regex-Treffer: %r", source, strat, m.group(1)[:80])
        return m.group(1).strip()
    except Exception as e:
        log.warning("[%s] %s -> Regex-Fehler: %s", source, strat, e)
        return None


# ---------------------------------------------------------------------------
# 1) ClearOutside  (https://clearoutside.com/forecast/<lat>/<lon>)
# ---------------------------------------------------------------------------
# WICHTIG (Erkenntnis aus Live-Test): Die Seite nutzt KEINE <table>, sondern
# div-basierte Zeilen. Die Werte stehen aber als Klartext im gerenderten Text:
#   "Total Clouds (% Sky Obscured)" -> 24 Stundenzahlen in einer Zeile
#   "Low Clouds" / "Medium Clouds" / "High Clouds" -> dito
#   "Rain" -> Regenwahrscheinlichkeit pro Stunde
# Strategie 1 (robust): page.inner_text("body") + Regex "Label -> Zahlenfolge"
# Strategie 2 (Fallback): XPath über Label-Text -> folgende Zellen
# Genommen wird jeweils das Maximum der aktuellen Stunde + 4 Folgestunden.
# ---------------------------------------------------------------------------

async def scrape_clearoutside(context, lat: float, lon: float, rep: SiteReport):
    source = "ClearOutside"
    url = f"https://clearoutside.com/forecast/{lat:.4f}/{lon:.4f}"
    page = await context.new_page()
    try:
        log.info("[%s] GET %s", source, url)
        # Kleine Zufallspause: reicht nicht fuer Cloudflare alleine, aber
        # reduziert Rate-Limit-Treffer wenn 3 Standorte hintereinander kommen
        await asyncio.sleep(random.uniform(1.5, 4.0))
        await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        # Kein table-Selektor mehr: nur warten, bis der Body überhaupt dasteht,
        # danach JS kurz Zeit lassen und den gerenderten TEXT parsen.
        log.info("[%s] Warte auf body (Werte stehen im Text, nicht in <table>)", source)
        await page.wait_for_selector("body", timeout=15000)
        await log_page_state(page, source)
        await page.wait_for_timeout(2500)  # Vue-App Werte nachladen lassen

        # Cloudflare-Retry: Challenge kurz Zeit geben, sich von selbst zu loesen
        try:
            if "Just a moment" in await page.title():
                log.warning("[%s] Cloudflare-Challenge -> warte 10 s und versuche "
                            "Seite neu zu laden", source)
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_timeout(10000)
                await log_page_state(page, source)
        except Exception:
            pass

        # --- Strategie 1: Text-Parse (layout-unabhaengig) ---
        body_text = ""
        try:
            body_text = await page.inner_text("body")
            log.info("[%s] Strategie 1 (inner_text): %d Zeichen Text geladen",
                     source, len(body_text))
        except Exception as e:
            log.warning("[%s] inner_text fehlgeschlagen: %s", source, type(e).__name__)

        def row_values_from_text(label: str, txt: str, limit: int = 5) -> list[int]:
            # Label ueberspringen (inkl. Klammerzusatz wie "(% Sky Obscured)"),
            # dann die erste zusammenhaengende Zahlenfolge >= 5 Werte greppen.
            # limit=5: jetzt+4h (Rating-Worst-Case); limit=24: volle Reihe
            # fuer die Vorausschau.
            pat = re.escape(label) + r"[^\d]*((?:\s*\d{1,3}){5,})"
            m = re.search(pat, txt)
            if not m:
                return []
            return [int(v) for v in re.findall(r"\d{1,3}", m.group(1))][:limit]

        if body_text:
            total = row_values_from_text("Total Clouds", body_text)
            low    = row_values_from_text("Low Clouds", body_text)
            mid    = row_values_from_text("Medium Clouds", body_text)
            high   = row_values_from_text("High Clouds", body_text)
            # Live-Check: die Regenwahrscheinlichkeit heisst hier
            # "Precipitation Probability (%)" (nicht "Rain"!)
            rain   = row_values_from_text("Precipitation Probability", body_text)
            log.info("[%s] Text-Parse: total=%s low=%s mid=%s high=%s rain=%s",
                     source, total, low, mid, high, rain)
            # Vorausschau-Reihe: volle 24 h ab aktueller Stunde, ts lokal
            n_fc = 24 - datetime.now().hour
            ft = row_values_from_text("Total Clouds", body_text, 24)
            fl = row_values_from_text("Low Clouds", body_text, 24)
            fm = row_values_from_text("Medium Clouds", body_text, 24)
            fh = row_values_from_text("High Clouds", body_text, 24)
            fr = row_values_from_text("Precipitation Probability", body_text, 24)
            today = datetime.now()
            rep.fc_clouds = [
                {"ts": (today + timedelta(hours=i)).isoformat(timespec="minutes"),
                 "total": ft[i] if i < len(ft) else None,
                 "low": fl[i] if i < len(fl) else None,
                 "mid": fm[i] if i < len(fm) else None,
                 "high": fh[i] if i < len(fh) else None,
                 "rain": fr[i] if i < len(fr) else None}
                for i in range(min(n_fc, len(ft)))]

        # --- Strategie 2: XPath-Fallback, falls Text-Parse leer blieb ---
        if not body_text or not any([total, low, mid, high, rain]):
            log.info("[%s] Strategie 2 (XPath-Fallback)", source)

            async def row_values(row_label: str) -> list[int]:
                xp = (f"xpath=//*[contains(., '{row_label}')]/"
                      f"following-sibling::*[1]")
                try:
                    cells = await page.eval_on_selector_all(
                        xp, "els => els.map(e => e.innerText.trim()).join(' ')")
                    log.info("[%s] XPath-Rohdaten: %s", source, str(cells)[:120])
                    vals = [int(v) for v in re.findall(r"\d{1,3}", cells or "")]
                    return vals[:5]
                except Exception as e:
                    log.warning("[%s] XPath fehlgeschlagen: %s", source, type(e).__name__)
                    return []

            if not total: total = await row_values("Total Clouds")
            if not low:    low = await row_values("Low Clouds")
            if not mid:    mid = await row_values("Medium Clouds")
            if not high:  high = await row_values("High Clouds")
            if not rain:  rain = await row_values("Precipitation Probability")

        # Maximum der naechsten 4 h als worst case
        if total: rep.clouds_total = max(total)
        if low:    rep.clouds_low = max(low)
        if mid:    rep.clouds_mid = max(mid)
        if high:   rep.clouds_high = max(high)
        if rain:   rep.rain_prob = max(rain)
        if total: rep.clouds_source = "clearoutside"
        log.info("[%s] Ergebnis: total=%s low=%s mid=%s high=%s rain=%s",
                 source, rep.clouds_total, rep.clouds_low, rep.clouds_mid,
                 rep.clouds_high, rep.rain_prob)
    except Exception as e:
        log.error("[%s] ABGESTÜRZT: %s", source, type(e).__name__)
        log.debug("[%s] Traceback:\n%s", source, traceback.format_exc())
        rep.errors.append(f"ClearOutside: {type(e).__name__}")
    finally:
        await page.close()


# ---------------------------------------------------------------------------
# 2) Meteoblue Astronomical Seeing
# ---------------------------------------------------------------------------
# Korrektes URL-Schema (Live-Check, altes ?query=-Schema war 404):
#   https://www.meteoblue.com/en/weather/forecast/seeing/49.47N8.58E
#   (S/W automatisch bei negativen Koordinaten)
# Die Seite rendert server-seitig eine Tabelle:
#   Zeile "Seeing [Arc Sec.]"    -> 24 Stundenzahlen (z.B. 0.92, 1.10, ...)
#   Zeile "Jet Stream ... [m/s]" -> Stundenzahlen in m/s
#   Zeile "Seeing Index ..."     -> Werte 1..5
# Wir parsen den sichtbaren Tabellentext (keine fragilen CSS-Klassen) und
# waehlen den Wert der aktuellen Stunde (min. 3 h voraus als Worst Case).
# ---------------------------------------------------------------------------

async def scrape_meteoblue(context, lat: float, lon: float, rep: SiteReport):
    source = "Meteoblue"
    page = await context.new_page()
    try:
        ns = "N" if lat >= 0 else "S"
        ew = "E" if lon >= 0 else "W"
        url = (f"https://www.meteoblue.com/en/weather/forecast/seeing/"
               f"{abs(lat):.2f}{ns}{abs(lon):.2f}{ew}")
        log.info("[%s] GET %s", source, url)
        await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await page.wait_for_selector("main, body", timeout=15000)
        await log_page_state(page, source)
        await page.wait_for_timeout(1500)

        # Sichtbaren Text nehmen, nicht das rohe HTML: Im HTML matchen
        # CSS/JS-Schnipsel und erzeugen False Positives.
        try:
            main_text = await page.inner_text("main")
        except Exception:
            main_text = await page.inner_text("body")
        log.info("[%s] Seitentext: %d Zeichen (Ausschnitt: %r)",
                 source, len(main_text), main_text[:200])

        # --- Tabellen-Parse (Struktur per Live-Inspektion ermittelt) ---
        # Jede Datenzeile: "Stunde Low Mid High ArcSec Idx1 Idx2 JetStream m/s ..."
        # Beispiel: "15  27  3  6  0.90  5  5  13 m/s 00.0 ..."
        # Tagesbloecke beginnen mit "Sat 2026-08-15" o. ae.
        ROW_RE = re.compile(
            r"^\s*(\d{1,2})\s+(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s+"
            r"(\d{1,2}[.,]\d{1,2})\s+([1-5])\s+([1-5])\s+(\d{1,3})\s*m/s",
            re.MULTILINE)
        DAY_RE = re.compile(r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[ ,]+\d{4}-\d{2}-\d{2}")

        segments = DAY_RE.split(main_text)
        # segments[0] = Tabellenkopf/Muell; danach ein Segment pro Tag
        day_rows = []
        for seg in segments[1:]:
            rows = [(int(h), float(s.replace(",", ".")), int(i1), int(i2), int(j))
                    for h, lo, mi, hi, s, i1, i2, j in ROW_RE.findall(seg)]
            day_rows.append(rows)
        if not any(day_rows):
            # Tabelle liegt evtl. ausserhalb <main> -> Body-Text nochmal pruefen
            body_text_fallback = await page.inner_text("body")
            segs = DAY_RE.split(body_text_fallback)
            day_rows = [[(int(h), float(s.replace(",", ".")), int(i1), int(i2), int(j))
                         for h, lo, mi, hi, s, i1, i2, j in ROW_RE.findall(seg)]
                        for seg in segs[1:]]
            log.info("[%s] <main> leer -> Body-Fallback genutzt", source)
        log.info("[%s] Tagesbloecke: %s", source,
                 [len(r) for r in day_rows])

        # Nacht-Fenster: jetzt bis heute 23 Uhr + morgen 0-5 Uhr, max 8 h
        h_now = datetime.now().hour
        night = []
        for day_idx, rows in enumerate(day_rows[:2]):
            for (h, seeing, i1, i2, jet) in rows:
                if (day_idx == 0 and h >= h_now) or (day_idx == 1 and h <= 5):
                    night.append((h, seeing, i1, i2, jet))
        night = night[:8]
        log.info("[%s] Nacht-Fenster (%dh+): %s", source, h_now, night[:8])

        # Vorausschau-Reihe: ALLE verfuegbaren Tagesbloecke (Meteoblue liefert
        # ohne Abo heute + 2 Folgetage) - Rating-Aggregation unveraendert
        fc_night = []
        base = datetime.now()
        for day_idx, rows in enumerate(day_rows):
            for (h, seeing, i1, i2, jet) in rows:
                ts = (base + timedelta(days=day_idx)).replace(
                    hour=h, minute=0, second=0, microsecond=0)
                fc_night.append({"ts": ts.isoformat(timespec="minutes"),
                                 "seeing": seeing, "idx": i1, "jet": jet})
        rep.fc_seeing = fc_night

        if night:
            # Worst Case fuer die Nacht: schlechtestes (max) Seeing,
            # max. Jetstream, min. Index -> konservative Go/No-Go-Entscheidung
            rep.seeing = max(n[1] for n in night)
            rep.jetstream = float(max(n[4] for n in night))
            rep.seeing_index = min(n[2] for n in night)
            rep.seeing_source = "meteoblue"
        elif day_rows and day_rows[0]:
            # Fallback: erste Stunde des heutigen Blocks
            h, seeing, i1, i2, jet = day_rows[0][0]
            rep.seeing, rep.jetstream, rep.seeing_index = seeing, float(jet), i1
            rep.seeing_source = "meteoblue"
            log.warning("[%s] Nacht-Fenster leer - nehme erste Tagesstunde", source)
        else:
            log.warning("[%s] keine Seeing-Zeilen gefunden", source)
        log.info("[%s] Ergebnis: seeing=%s jetstream=%s index=%s",
                 source, rep.seeing, rep.jetstream, rep.seeing_index)
    except Exception as e:
        log.error("[%s] ABGESTÜRZT: %s", source, type(e).__name__)
        log.debug("[%s] Traceback:\n%s", source, traceback.format_exc())
        rep.errors.append(f"Meteoblue: {type(e).__name__}")
    finally:
        await page.close()


# ---------------------------------------------------------------------------
# 3) Radar/Stormtracking: DWD-Unwetterwarnungen (offiziell) + Bright Sky
# ---------------------------------------------------------------------------
# Kachelmannwetter.com wurde bewusst ERSETZT: Akamai-Bot-Schutz blockt
# headless Chrome dauerhaft ("Access Denied", Live-Check 2026-08). Stattdessen
# die amtliche Primärquelle, aus der Kachelmann selbst speist:
#   DWD-Geoserver WFS (kostenlos, kein Key, kein Bot-Schutz):
#   https://maps.dwd.de/geoserver/dwd/ows?...typeName=dwd:Warnungen_Gemeinden
#   -> GeoJSON aller aktiven Warnungen; wir filtern per BBOX (~15 km Radius)
#   und pruefen Punkt-in-Polygon, ob der Standort wirklich im Warngebiet liegt.
# EVENT-Mapping: GEWITTER -> Storm Alert | REGEN/NIEDERSCHLAG/SCHNEE -> Rain
# Zusaetzlich (2. Saeule): Bright Sky API (DWD-Rohdaten) Niederschlag naechste 2h.
# ---------------------------------------------------------------------------

DWD_WFS_URL = (
    "https://maps.dwd.de/geoserver/dwd/ows"
    "?service=WFS&version=2.0.0&request=GetFeature"
    "&typeName=dwd%3AWarnungen_Gemeinden&outputFormat=application%2Fjson"
)

# Unwetter-Events, die fuers Teleskop relevant sind (Hitze/Wind ignorieren wir)
STORM_KEYWORDS = ("GEWITTER",)
RAIN_KEYWORDS = ("REGEN", "NIEDERSCHLAG", "SCHNEE", "STARKREGEN")


def point_in_poly(lon: float, lat: float, ring) -> bool:
    """Ray-Casting: liegt (lon, lat) im Polygon-Ring [(lon,lat),...]?"""
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def poly_contains(geometry: dict, lon: float, lat: float) -> bool:
    """GeoJSON Polygon/MultiPolygon-Membership; True wenn Geometrie fehlt."""
    gtype = (geometry or {}).get("type")
    if gtype == "Polygon":
        rings = [(geometry["coordinates"] or [[]])[0]]
    elif gtype == "MultiPolygon":
        rings = [poly[0] for poly in (geometry["coordinates"] or [[[]]])]
    else:
        return True  # keine Geometrie -> BBOX-Treffer zaehlt als Warnung
    return any(point_in_poly(lon, lat, r) for r in rings if len(r) >= 3)


def http_get_json(url: str, timeout: int = 15, retries: int = 2) -> dict:
    """HTTP-GET mit Retry: fängt kurzzeitige Aussetzer (Standby-Wakeup,
    WLAN-Reconnect, DNS-Hickser) ab, damit ein Lauf nicht gleich abbricht."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        except Exception as e:
            last_exc = e
            if attempt < retries:
                log.info("[HTTP] Versuch %d/%d fehlgeschlagen (%s) - Retry in 5 s",
                         attempt, retries, type(e).__name__)
                time.sleep(5)
    raise last_exc


def check_dwd_warnings(lat: float, lon: float, rep: SiteReport):
    """Aktive DWD-Warnungen im ~15 km Radius abfragen und zuordnen."""
    source = "DWD"
    try:
        # ~15 km Radius um die Koordinaten (BBOX-Reihenfolge: lon,lat,lon,lat)
        bbox = f"{lon - 0.20:.4f},{lat - 0.15:.4f},{lon + 0.20:.4f},{lat + 0.15:.4f}"
        url = f"{DWD_WFS_URL}&bbox={urllib.parse.quote(bbox + ',EPSG:4326')}"
        log.info("[%s] GET Warnungen (BBOX %s)", source, bbox)
        data = http_get_json(url, timeout=15)

        storm, rain, hits = False, False, []
        for f in data.get("features", []):
            p = f.get("properties", {})
            event = (p.get("EVENT") or "").upper()
            if not event:
                continue
            # Nur zaehlen, wenn der Standort wirklich im Warn-Polygon liegt
            if poly_contains(f.get("geometry") or {}, lon, lat):
                hits.append(event)
                if any(k in event for k in STORM_KEYWORDS):
                    storm = True
                elif any(k in event for k in RAIN_KEYWORDS):
                    rain = True

        log.info("[%s] Warnungen im Radius: %s", source, hits or "keine")
        if storm:
            rep.radar_status = "Storm Alert"
        elif rain:
            rep.radar_status = "Rain Alert"
        else:
            rep.radar_status = "Clear"
    except Exception as e:
        log.warning("[%s] Warnungs-Abfrage fehlgeschlagen: %s", source, type(e).__name__)
        log.debug("[%s] Traceback:\n%s", source, traceback.format_exc())
        rep.radar_status = "Unknown"


def check_brightsky_ground(lat: float, lon: float, rep: SiteReport):
    """2. Saeule: Niederschlag der naechsten 2 h (DWD-Rohdaten via Bright Sky)
    plus Bodendaten fuer die Historie: max. Wind (km/h) und min. Taupunkt-
    Spread (K) - Spread < ~2-3 K ueber Stunden = Beschlagsrisiko Optik.
    Laeuft IMMER (auch bei aktiver DWD-Warnung), damit Wind/Taupunkt
    lueckenlos in der DB landen."""
    try:
        now = datetime.now(timezone.utc)
        params = urllib.parse.urlencode({
            "lat": lat, "lon": lon,
            "date": now.strftime("%Y-%m-%dT%H:%M"),
            "last_date": (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
        })
        log.info("[BrightSky] GET api.brightsky.dev/weather?%s", params)
        data = http_get_json(f"https://api.brightsky.dev/weather?{params}", timeout=10)
        rows = data.get("weather", [])

        precip = sum(w.get("precipitation") or 0 for w in rows)
        rep.precip_2h = round(precip, 2)
        # Stündliches Wolken-Array der naechsten 2 h mitfuehren (fuer /clear)
        rep.clouds_2h = [w.get("cloud_cover") for w in rows]
        # Boeen-Max im 2-h-Fenster: frisch je Tick (dieser Call ist ungecachtet)
        # - Basis fuer die Wind-Eskalation mit echtem 2-Tick-Debounce
        g2 = [w["wind_gust_speed"] for w in rows if w.get("wind_gust_speed") is not None]
        rep.gusts_2h = max(g2) if g2 else None
        winds = [w["wind_speed"] for w in rows if w.get("wind_speed") is not None]
        if winds:
            rep.wind_speed = max(winds)
        spreads = [w["temperature"] - w["dew_point"] for w in rows
                   if w.get("temperature") is not None
                   and w.get("dew_point") is not None]
        if spreads:
            rep.dewpoint_spread = round(min(spreads), 1)
        log.info("[BrightSky] 2h: precip=%.2f mm | wind_max=%s km/h | tau-spread_min=%s K",
                 precip, rep.wind_speed, rep.dewpoint_spread)

        # Niederschlag hebt nur an, wenn keine staerkere DWD-Warnung vorliegt
        if precip > 0.1 and rep.radar_status in ("Clear", "Unknown"):
            rep.radar_status = "Rain Alert"
        elif rep.radar_status == "Unknown":
            rep.radar_status = "Clear"
    except Exception as e:
        log.warning("[BrightSky] Abfrage fehlgeschlagen: %s", type(e).__name__)
        log.debug("[BrightSky] Traceback:\n%s", traceback.format_exc())


def check_open_meteo_clouds(lat: float, lon: float, rep: SiteReport):
    """Open-Meteo - ab sofort ZWEITQUELLE mit Pflicht-Abruf (nicht mehr nur
    ClearOutside-Fallback): die 72-h-Wolkenreihe deckt die Naechte 2+3 der
    Vorausschau, da ClearOutside nur ~24 h liefert. Kein Bot-Schutz, haengt
    an keinem Cloudflare-Budget. Rating-Kaskade unveraendert:
    ClearOutside -> Open-Meteo (Schichten) -> BrightSky (nur Total)."""
    try:
        params = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon,
            "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,"
                      "cloud_cover_high,precipitation_probability,"
                      "wind_speed_300hPa",   # Jetstream-Proxy (Meteoblue-Fallback)
            "forecast_days": 4, "timezone": "Europe/Berlin",  # 3 Naechte
        })
        log.info("[OpenMeteo] GET api.open-meteo.com/v1/forecast?%s", params)
        data = http_get_json(f"https://api.open-meteo.com/v1/forecast?{params}",
                             timeout=10)
        h = data.get("hourly", {})
        now_h = datetime.now().hour
        # Worst Case ueber aktuelle Stunde + 4 h (wie ClearOutside-Behandlung)
        win = range(now_h, min(now_h + 5, len(h.get("time", []))))

        def wmax(key):
            vals = [h[key][i] for i in win
                    if i < len(h.get(key, [])) and h[key][i] is not None]
            return max(vals) if vals else None

        total = wmax("cloud_cover")
        low, mid, high = wmax("cloud_cover_low"), wmax("cloud_cover_mid"), \
            wmax("cloud_cover_high")

        # 72-h-Reihe IMMER (deckt Naechte 2+3; Nacht 1 wird in build_forecast
        # durch die ClearOutside-Reihe verfeinert, wenn vorhanden)
        times = h.get("time", [])
        now_h = datetime.now().hour
        om_series = []
        for i in range(now_h, min(now_h + 72, len(times))):
            def g(key, i=i):
                v = h.get(key) or []
                return v[i] if i < len(v) and v[i] is not None else None
            om_series.append({"ts": times[i], "total": g("cloud_cover"),
                              "low": g("cloud_cover_low"),
                              "mid": g("cloud_cover_mid"),
                              "high": g("cloud_cover_high"),
                              "rain": g("precipitation_probability")})
        if om_series:
            rep.fc_clouds_om = om_series

        if total is not None and rep.clouds_total is None:
            rep.clouds_total, rep.clouds_low = total, low
            rep.clouds_mid, rep.clouds_high = mid, high
            rep.clouds_source = "open_meteo"
            if rep.rain_prob is None:
                rep.rain_prob = wmax("precipitation_probability")
            log.info("[OpenMeteo] Schicht-Fallback: total=%s L/M/H=%s/%s/%s "
                     "rain=%s", total, low, mid, high, rep.rain_prob)
            # Wenn ClearOutside nichts lieferte, ist OM auch die Nacht-1-Serie
            if not rep.fc_clouds:
                rep.fc_clouds = om_series
        # Jetstream-Fallback (nur falls Meteoblue nichts lieferte): Wind in
        # 300 hPa als Proxy - liegt etwas tiefer als Meteoblues 200-hPa-Jet,
        # liefert aber die Groessenordnung. OM gibt km/h -> m/s.
        if rep.jetstream is None:
            jet = wmax("wind_speed_300hPa")
            if jet is not None:
                rep.jetstream = round(jet / 3.6, 1)
                log.info("[OpenMeteo] Jetstream-Proxy (300 hPa): %s m/s",
                         rep.jetstream)
    except Exception as e:
        log.warning("[OpenMeteo] Abfrage fehlgeschlagen: %s", type(e).__name__)
        log.debug("[OpenMeteo] Traceback:\n%s", traceback.format_exc())


def check_brightsky_clouds(lat: float, lon: float, rep: SiteReport):
    """Fallback fuer ClearOutside (Cloudflare-Challenge/Rate-Limit): Gesamt-
    bewolkung der naechsten 4 h aus DWD-Rohdaten (Bright Sky, cloud_cover %).
    Low/Mid/High-Aufteilung liefert diese Quelle nicht - bleibt dann n/a."""
    if rep.clouds_total is not None:
        return
    try:
        now = datetime.now(timezone.utc)
        params = urllib.parse.urlencode({
            "lat": lat, "lon": lon,
            "date": now.strftime("%Y-%m-%dT%H:%M"),
            "last_date": (now + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M"),
        })
        log.info("[BrightSky] Cloud-Fallback GET api.brightsky.dev/weather?%s", params)
        data = http_get_json(f"https://api.brightsky.dev/weather?{params}", timeout=10)
        covers = [w["cloud_cover"] for w in data.get("weather", [])
                  if w.get("cloud_cover") is not None]
        if covers:
            rep.clouds_total = max(covers)
            rep.clouds_source = "brightsky_fallback"
            log.info("[BrightSky] Cloud-Fallback: cloud_cover max 4h = %s%%",
                     rep.clouds_total)
    except Exception as e:
        log.warning("[BrightSky] Cloud-Fallback fehlgeschlagen: %s", type(e).__name__)


NIGHT_CACHE_PATH = os.path.expanduser("~/.astro_crawler_night.json")
NIGHT_TTL_MIN = 30  # Boden-Nachtverlauf aendert sich langsam -> selten abfragen


def _rh_from_dew(t_c: Optional[float], td_c: Optional[float]) -> Optional[int]:
    """Rel. Luftfeuchte aus Temp+Taupunkt (Magnus-Naeherung). BrightSky
    liefert relative_humidity in Vorhersage-Zeilen oft als None, dew_point
    aber immer."""
    import math
    if t_c is None or td_c is None:
        return None
    a, b = 17.62, 243.12
    gamma = (a * td_c / (b + td_c)) - (a * t_c / (b + t_c))
    return max(0, min(100, round(100 * math.exp(gamma))))


def check_brightsky_night(lat: float, lon: float, rep: SiteReport):
    """Nachtverlauf aus BrightSky - Extra-Infos fuer das Equipment (600D
    ungekuehlt: Temp; EQ5: Boeen; offener Newton: Feuchte/klarste Stunde).
    Nur NACHTSTUNDEN (lokal 20-06 Uhr) gehen in min/max ein - das +11h-Fenster
    waere sonst durch den heissen Nachmittag verfaelscht.
    TTL-gecacht pro Standort (1 Call je 30 Min genuegt dem 5-Min-Radar-Timer)."""
    cache = {}
    try:
        with open(NIGHT_CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        pass
    key = f"{lat:.3f}|{lon:.3f}"
    entry = cache.get(key)
    if entry:
        age = (datetime.now() - datetime.fromisoformat(entry["ts"])
               ).total_seconds() / 60
        if age < NIGHT_TTL_MIN:
            rep.night_temp_min = entry["temp_min"]
            rep.night_temp_max = entry["temp_max"]
            rep.night_rh_max = entry["rh_max"]
            rep.night_cloud_min = entry["cloud_min"]
            rep.wind_gusts = entry["gusts_max"]
            rep.fc_ground = entry.get("hours")
            return

    try:
        now = datetime.now(timezone.utc)
        # 56-h-Fenster: deckt 3 Nächte für die Vorausschau (hours-Reihe).
        # Die min/max-Aggregate unten bleiben auf die ERSTE Nacht begrenzt.
        params = urllib.parse.urlencode({
            "lat": lat, "lon": lon,
            "date": now.strftime("%Y-%m-%dT%H:%M"),
            "last_date": (now + timedelta(hours=56)).strftime("%Y-%m-%dT%H:%M"),
        })
        log.info("[BrightSky-Nacht] GET ?%s", params)
        data = http_get_json(f"https://api.brightsky.dev/weather?{params}",
                             timeout=10)
        rows = data.get("weather", [])
        # Erste Nacht = Zeitraum bis zum ersten Sonnenaufgang (~11 h ab jetzt)
        first_night_cutoff = datetime.fromisoformat(
            rows[0]["timestamp"]) if rows else now
        first_night_cutoff = (first_night_cutoff.astimezone(_berlin())
                              + timedelta(hours=12))

        # Nur echte Nachtstunden zaehlen (lokale Berliner Zeit 20:00-05:59)
        night_rows = []
        for w in rows:
            try:
                local = datetime.fromisoformat(w["timestamp"]).astimezone(
                    _berlin())
            except Exception:
                continue
            if (local.hour >= 20 or local.hour < 6) and local < first_night_cutoff:
                night_rows.append(w)
        if not night_rows:
            night_rows = rows  # Fallback: alles (frueh morgens gecrawlt)

        temps = [w["temperature"] for w in night_rows
                 if w.get("temperature") is not None]
        clouds = [w["cloud_cover"] for w in night_rows
                  if w.get("cloud_cover") is not None]
        # Boeen: BrightSky-Feld heisst wind_gust_speed
        gusts = [w["wind_gust_speed"] for w in night_rows
                 if w.get("wind_gust_speed") is not None]
        # Feuchte: direkt, sonst aus T/Td berechnen
        rhs = [w["relative_humidity"] for w in night_rows
               if w.get("relative_humidity") is not None]
        if not rhs:
            rhs = [_rh_from_dew(w.get("temperature"), w.get("dew_point"))
                   for w in night_rows]
            rhs = [r for r in rhs if r is not None]

        entry = {"ts": datetime.now().isoformat(timespec="seconds"),
                 "temp_min": min(temps) if temps else None,
                 "temp_max": max(temps) if temps else None,
                 "rh_max": max(rhs) if rhs else None,
                 "cloud_min": min(clouds) if clouds else None,
                 "gusts_max": max(gusts) if gusts else None}
        # Stündliche Reihe fuer die Vorausschau (Wind/Tau/Regen je Stunde);
        # Taupunkt-Spread je Stunde aus T/Td (rh-Fallback-Logik analog)
        hours = []
        for w in rows:
            t, td, wnd = w.get("temperature"), w.get("dew_point"), w.get("wind_speed")
            hours.append({
                "ts": w.get("timestamp"),
                "cloud": w.get("cloud_cover"),
                "precip": w.get("precipitation"),
                "prob": w.get("precipitation_probability"),
                "wind": wnd,
                "tau": round(t - td, 1) if t is not None and td is not None else None,
                "temp": t,
            })
        entry["hours"] = hours
        cache[key] = entry
        try:
            with open(NIGHT_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=1)
        except Exception as e:
            log.warning("[BrightSky-Nacht] Cache-Schreiben fehlgeschlagen: %s", e)
        rep.night_temp_min, rep.night_temp_max = entry["temp_min"], entry["temp_max"]
        rep.night_rh_max, rep.night_cloud_min = entry["rh_max"], entry["cloud_min"]
        rep.wind_gusts = entry["gusts_max"]
        rep.fc_ground = entry.get("hours")
        log.info("[BrightSky-Nacht] temp %s..%s°C | rh_max %s%% | cloud_min %s%% "
                 "| boeen_max %s km/h", entry["temp_min"], entry["temp_max"],
                 entry["rh_max"], entry["cloud_min"], entry["gusts_max"])
    except Exception as e:
        log.warning("[BrightSky-Nacht] Abfrage fehlgeschlagen: %s", type(e).__name__)
        log.debug("[BrightSky-Nacht] Traceback:\n%s", traceback.format_exc())


async def scrape_radar(lat: float, lon: float, rep: SiteReport):
    """Radar-Saeule: DWD-Warnungen + BrightSky-Bodendaten (kein Browser noetig)."""
    await asyncio.to_thread(check_dwd_warnings, lat, lon, rep)
    await asyncio.to_thread(check_brightsky_ground, lat, lon, rep)
    await asyncio.to_thread(check_brightsky_night, lat, lon, rep)


# ---------------------------------------------------------------------------
# Proaktive Alarm-Logik (Zustandsdatei verhindert Telegram-Spam)
# ---------------------------------------------------------------------------
# Gesendet wird NUR bei Ereignissen:
#   - Rain/Storm Alert am Radar (mit Cooldown, außer Eskalation Rain->Storm)
#   - Rating-Wechsel (z.B. GO -> NO-GO oder NO-GO -> GO/MAYBE)
# Routine-Dashboards gehen nur ins lokale Log, nicht nach Telegram.

STATE_PATH = os.path.expanduser("~/.astro_crawler_state.json")
WATCHLIST_PATH = os.path.expanduser("~/.astro_crawler_watchlist.json")
DB_PATH = os.path.expanduser("~/.astro_crawler.db")
MOON_CACHE_PATH = os.path.expanduser("~/.astro_crawler_moon.json")
FORECAST_PATH = os.path.expanduser("~/.astro_crawler_forecast.json")
SKYFIELD_DIR = os.path.expanduser("~/.skyfield")
MOON_MIN_ALT = 30.0  # Grad: darunter gilt der Mond als 'zu niedrig' (Extinktion)
ALERT_COOLDOWN_MIN = 90  # gleicher Alarm am gleichen Ort nicht vor Ablauf erneut


def load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"ratings": {}, "last_alert": {}}


def save_state(state: dict):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log.warning("[State] Konnte %s nicht schreiben: %s", STATE_PATH, e)


def get_profile(state: dict) -> str:
    return state.get("profile", "dso")


def set_profile(profile: str):
    """Profil global setzen. Ratings werden zurueckgesetzt, damit der
    Profilwechsel keine Kette falscher 'Rating hat sich geaendert'-Alarme
    auslöst (die Schwellen sind ja andere geworden)."""
    state = load_state()
    state["profile"] = profile
    state["ratings"] = {}
    save_state(state)
    log.info("[Profil] gewechselt auf '%s' (Ratings zurueckgesetzt)", profile)


def evaluate_alerts(reports: list[SiteReport], state: dict,
                    radar_only: bool = False,
                    profile: str = "dso") -> Optional[str]:
    """Prüft auf Ereignisse; liefert Alarm-Text oder None. Aktualisiert state.

    Zwei Betriebsarten:
      radar_only=True  (5-Min-Radar-Timer): NUR Radar-Ereignisse - neuer
        Gewitter-/Regen-Alarm sowie Entwarnung. Ratings werden weder gelesen
        noch geschrieben (die Wolken-/Seeing-Daten fehlen hier ja) - die
        letzte Heavy-Bewertung bleibt unangetastet.
      radar_only=False (30-Min-Heavy-Timer): zusätzlich Rating-Wechsel.

    Robustheit: 'Unknown' (Netz weg, API down) überschreibt NIE den letzten
    bekannten Radar-Status -> keine Flatter-Alarme nach Aussetzern.
    """
    now = datetime.now()
    state.setdefault("radar", {})
    state.setdefault("ratings", {})
    state.setdefault("last_alert", {})
    alerts = []

    for r in reports:
        rating, icon = r.rate(profile)
        prev_rating = state["ratings"].get(r.name)
        prev_radar = state["radar"].get(r.name)

        event = None
        cooldown_key = r.name
        escalate = False  # True = Cooldown ignorieren (Eskalation/Neuigkeit)

        if "Storm" in r.radar_status:
            event = f"{icon} GEWITTER-ALARM: {r.name} (DWD-Warnung aktiv)"
            cooldown_key += "|storm"
            # Neu oder Eskalation (Rain->Storm) sofort melden, ohne Cooldown
            escalate = prev_radar != "Storm Alert"
        elif "Rain" in r.radar_status:
            event = f"{icon} REGEN-ALARM: {r.name} (Niederschlag gemeldet)"
            cooldown_key += "|rain"
        elif (prev_radar in ("Rain Alert", "Storm Alert")
              and r.radar_status == "Clear"):
            event = f"✅ {r.name}: Entwarnung - Radar wieder Clear"
            cooldown_key += "|clear"

        # Bekannten Status persistieren; 'Unknown' nie speichern (Flatterschutz)
        if r.radar_status != "Unknown":
            state["radar"][r.name] = r.radar_status

        # Rating-Logik nur im Heavy-Modus (Radar-Modus hat keine Wolken/Seeing)
        if not radar_only:
            if not event and prev_rating is not None and rating != prev_rating:
                if rating == "NO-GO":
                    event = (f"{icon} {r.name}: jetzt NO-GO "
                             f"(Clouds {r.clouds_total}%, Seeing {r.seeing}\")")
                elif prev_rating == "NO-GO" and rating in ("GO", "MAYBE"):
                    event = f"{icon} {r.name}: wieder {rating} - Himmel hat sich beruhigt"
                cooldown_key += f"|{rating}"
            state["ratings"][r.name] = rating

        if not event:
            continue

        # Cooldown pruefen (ausser Eskalation/Entwarnung)
        la = state["last_alert"].get(cooldown_key)
        if la and not escalate and not cooldown_key.endswith("|clear"):
            age_min = (now - datetime.fromisoformat(la)).total_seconds() / 60
            if age_min < ALERT_COOLDOWN_MIN:
                log.info("[Alert] Cooldown aktiv (%.0f min): %s", age_min, event)
                continue

        state["last_alert"][cooldown_key] = now.isoformat()
        alerts.append(event)

    return "\n".join(alerts) if alerts else None




# ---------------------------------------------------------------------------
# Meilenstein-Alarm: einmalige Meldung ab ausreichend /rate-Sessions
# (Gradient Boosted Trees braucht ~50 Samples bei unseren ~8 Features)
# ---------------------------------------------------------------------------

MILESTONE_INTERMEDIATE = 20   # Halbzeit-Info
MILESTONE_FINAL = 50          # "Genug Daten fuer ein erstes Modell"


def check_milestones():
    """1x taeglich im Radar-Zyklus: Feedback-Anzahl pruefen und einmalig
    melden. Flags + Check-Datum persistieren in der State-Datei."""
    state = load_state()
    today = f"{datetime.now():%Y-%m-%d}"
    if state.get("milestone_check_date") == today:
        return  # heute schon geprueft
    state["milestone_check_date"] = today

    try:
        conn = sqlite3.connect(DB_PATH)
        n = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        conn.close()
    except Exception as e:
        log.warning("[Milestone] DB-Check fehlgeschlagen: %s", e)
        save_state(state)
        return

    msg = None
    if n >= MILESTONE_FINAL and not state.get("milestone_50_sent"):
        state["milestone_50_sent"] = True
        msg = (f"Genug Daten fuer ein erstes Modell - {n} Sessions bewertet.\n"
               f"Gradient Boosted Trees kann starten (Features: Wolken/Seeing/"
               f"Jet/Wind/Tau/Radar/Mond). Auswertung via /rate weiter "
               f"erwuenscht.")
    elif n >= MILESTONE_INTERMEDIATE and not state.get("milestone_20_sent"):
        state["milestone_20_sent"] = True
        msg = (f"Halbzeit: {n} von ~50 Sessions bewertet - Sammeln laeuft. "
               f"Weiter /rate nutzen!")
    save_state(state)
    if msg:
        log.info("[Milestone] %d Sessions -> Telegram", n)
        send_telegram(f"ASTRO-CRAWLER MILESTONE\n{datetime.now():%d.%m. %H:%M}\n\n{msg}")


# ---------------------------------------------------------------------------
# Clear-Sky-Alarm (/clear): Push, wenn Bewoelkung <20% fuer 2h UND echte Nacht
# ---------------------------------------------------------------------------

CLEAR_MAX_CLOUD_PCT = 20   # Schwelle "klar" (max der naechsten 2 Stunden)
CLEAR_WATCH_TTL_H = 24     # Watch läuft maximal einen Tag, dann einmalige Info


def _in_time_window(now: datetime, window: str) -> bool:
    """Liegt 'now' (HH:MM) im Fenster 'HH:MM-HH:MM' (auch über Mitternacht)?"""
    try:
        s, e = window.split("-")
        sh, sm = map(int, s.split(":"))
        eh, em = map(int, e.split(":"))
        t = now.hour * 60 + now.minute
        a, b = sh * 60 + sm, eh * 60 + em
        return a <= t <= b if a <= b else (t >= a or t <= b)
    except Exception:
        return False


def check_clear_alert(reports: list):
    """Im Radar-Takt: /clear-Bedingung pruefen und einmalig pushen.
    Bedingung: max(Bewoelkung naechste 2 h) < 20% UND astronomische Nacht
    (Sonne < -18 Grad, via skyfield dark_window). Danach Flag automatisch weg."""
    state = load_state()
    cw = state.get("clear_watch")
    if not cw:
        return
    now = datetime.now()
    try:
        age_h = (now - datetime.fromisoformat(cw["set"])).total_seconds() / 3600
    except Exception:
        age_h = 999.0
    if age_h > CLEAR_WATCH_TTL_H:
        del state["clear_watch"]
        save_state(state)
        log.info("[Clear] Watch abgelaufen (%s)", cw["name"])
        send_telegram(f"ℹ️ Clear-Watch für {cw['name']} nach 24 h abgelaufen "
                      f"(kein Fenster gefunden).")
        return

    rep = next((r for r in reports if r.name == cw["name"]), None)
    if rep is None or not rep.clouds_2h:
        return
    clouds = [c for c in rep.clouds_2h if c is not None]
    if not clouds:
        return
    dark_ok = bool(rep.dark_window) and _in_time_window(now, rep.dark_window)
    clear_ok = max(clouds) < CLEAR_MAX_CLOUD_PCT
    if clear_ok and dark_ok:
        del state["clear_watch"]
        save_state(state)
        log.info("[Clear] Fenster erkannt bei %s: max %s%% in 2h, dunkel %s",
                 cw["name"], max(clouds), rep.dark_window)
        send_telegram(
            f"✨ Clear Sky Window at {cw['name']}!\n"
            f"Bewölkung < 20% für die nächsten 2 Stunden "
            f"(max. {max(clouds)}%, Stundenwerte: {clouds}).\n"
            f"Astronomische Nacht bis {rep.dark_window.split('-')[1]}.")
    else:
        log.info("[Clear] noch nicht: max %s%% ( <%s gefordert), dunkel=%s",
                 max(clouds), CLEAR_MAX_CLOUD_PCT, dark_ok)


# ---------------------------------------------------------------------------
# Wind-Eskalation (sicherheitsrelevant): nur bei AKTIVER Session aktiv.
#   >40 km/h Boeen -> sofortige Warnung (60-min Cooldown, kein Debounce)
#   >60 km/h Boeen -> Abbruch-Push, aber erst nach Bestaetigung im zweiten
#                    Radar-Tick (~10 min) - eine Ausreisserboe allein soll
#                    keine Panik-Nachricht mitten in einer ruhigen Nacht
#                    ausloesen. Basis ist gusts_2h (frisch je Tick).
# ---------------------------------------------------------------------------

WIND_WARN_KMH = 40.0
WIND_ABORT_KMH = 60.0
WIND_WARN_COOLDOWN_MIN = 60


def check_wind_alert(reports: list):
    sess = db_open_session()
    if not sess:
        return
    session_loc = sess[2]
    # Session-Standort bevorzugt, sonst Maximum ueber alle Standorte
    rep = next((r for r in reports if r.name == session_loc), None)
    if rep is None:
        cand = [r for r in reports if r.gusts_2h is not None]
        rep = max(cand, key=lambda r: r.gusts_2h) if cand else None
    if rep is None or rep.gusts_2h is None:
        return
    gusts = rep.gusts_2h
    now = datetime.now()
    state = load_state()
    state.setdefault("last_alert", {})

    if gusts > WIND_ABORT_KMH:
        first_seen = state.get("wind60_since")
        if not first_seen:
            state["wind60_since"] = now.isoformat(timespec="seconds")
            save_state(state)
            log.warning("[Wind] Boeen %.0f km/h > 60 - warte auf Bestaetigung "
                        "im naechsten Radar-Tick (%s)", gusts, rep.name)
            return
        age_min = (now - datetime.fromisoformat(first_seen)).total_seconds() / 60
        if age_min >= 4:  # naechster 5-min-Tick hat dazwischen gelegen
            state.pop("wind60_since", None)
            state["last_alert"]["wind40"] = now.isoformat(timespec="seconds")
            save_state(state)
            log.warning("[Wind] ABBRUCH: Boeen %.0f km/h bestaetigt (%.0f min)",
                        gusts, age_min)
            send_telegram(
                f"🛑 Windböen >60 km/h! EQ5 Pro gefährdet. Teleskop "
                f"einpacken.\n[{rep.name}] {gusts:.0f} km/h bestätigt seit "
                f"{age_min:.0f} min (Session seit {sess[1][11:16]} aktiv).")
        else:
            save_state(state)  # noch nicht genug Zeit - weiter beobachten
            log.info("[Wind] Boeen %.0f km/h, Bestaetigung seit %.0f min - "
                     "warte", gusts, age_min)
        return

    # unter Abbruchschwelle: Debounce-State resetten
    state.pop("wind60_since", None)

    if gusts > WIND_WARN_KMH:
        la = state["last_alert"].get("wind40")
        if la and (now - datetime.fromisoformat(la)).total_seconds() / 60 \
                < WIND_WARN_COOLDOWN_MIN:
            save_state(state)
            log.info("[Wind] Warnung %.0f km/h - Cooldown aktiv", gusts)
            return
        state["last_alert"]["wind40"] = now.isoformat(timespec="seconds")
        save_state(state)
        log.warning("[Wind] Warnung: Boehen %.0f km/h (%s)", gusts, rep.name)
        send_telegram(
            f"⚠️ Windböen {gusts:.0f} km/h am Standort {rep.name}.\n"
            f"Montierung im Blick behalten - Abbruchgrenze liegt bei "
            f"{WIND_ABORT_KMH:.0f} km/h.")
    else:
        save_state(state)
        log.info("[Wind] ok: %.0f km/h (%s)", gusts, rep.name)


# ---------------------------------------------------------------------------
# Mond-Ephemeriden (skyfield, komplett lokal - kein API, kein Rate-Limit)
# ---------------------------------------------------------------------------
# de421.bsp (17 MB) liegt in ~/.skyfield und wird NIE neu geladen. Pro Standort
# und Nacht wird berechnet: Illumination %, max. Hoehe, Kulminationszeitpunkt,
# Zeitfenster Hoehe > 30 Grad (darunter: zu viel Extinktion/Bodennaesse),
# Mondauf-/-untergang. Ergebnis wird pro Tag+Standort gecacht (~/.astro_crawler_
# moon.json), d.h. der 5-Minuten-Timer kostet nach dem ersten Lauf einer Nacht
# praktisch nichts mehr. Reines INFO-Feld - der Mond ist je nach Ziel
# (Planeten/Mond vs. Deep-Sky) Freund oder Feind und geht NICHT ins Rating.

BERLIN_TZ = None  # lazy


def _berlin():
    global BERLIN_TZ
    if BERLIN_TZ is None:
        from zoneinfo import ZoneInfo
        BERLIN_TZ = ZoneInfo("Europe/Berlin")
    return BERLIN_TZ


def _fmt_hm(skyfield_time) -> str:
    dt = skyfield_time.utc_datetime().replace(tzinfo=timezone.utc)
    return dt.astimezone(_berlin()).strftime("%H:%M")


def _night_window(load, eph, ts, topo, t_now=None):
    """(start, sunrise) der KOMMENDEN ODER LAUFENDEN Nacht.
    Fix 17.08.: 'Nacht laeuft' gilt nur, wenn das LETZTE Sonnen-Event vor
    'now' ein Untergang war ( vorher genügte 'irgendein Untergang <= now',
    was nachtschlagend um 20:30 den gestrigen Untergang fand und Tag wie
    Nacht behandelte). Liegt der letzte Aufgang spaeter, ist es Tag und
    die Nacht beginnt beim naechsten Untergang."""
    from skyfield import almanac
    if t_now is None:
        t_now = ts.now()
    t_lo = ts.tt_jd(t_now.tt - 1.0)
    t_hi = ts.tt_jd(t_now.tt + 1.5)
    f = almanac.risings_and_settings(eph, eph["sun"], topo)
    t_ev, y_ev = almanac.find_discrete(t_lo, t_hi, f)
    events = sorted([(t.tt, y) for t, y in zip(t_ev, y_ev) if t.tt <= t_now.tt])
    sunrises = [t for t, y in zip(t_ev, y_ev) if y == 1]
    if events and events[-1][1] == 0:
        # Nacht laeuft bereits: jetzt bis zum naechsten Sonnenaufgang
        nxt = [t for t in sunrises if t.tt > t_now.tt]
        return t_now, (min(nxt) if nxt else ts.tt_jd(t_now.tt + 0.5))
    sunsets = [t for t, y in zip(t_ev, y_ev) if y == 0]
    nxt_set = [t for t in sunsets if t.tt > t_now.tt]
    start = min(nxt_set) if nxt_set else t_now
    nxt_rise = [t for t in sunrises if t.tt > start.tt]
    return start, (min(nxt_rise) if nxt_rise else ts.tt_jd(start.tt + 0.5))


def compute_moon(lat: float, lon: float) -> dict:
    """Nacht-Astronomie fuer eine Location: Mond + Dunkelheit + Planeten.

    Nutzt NUR die bereits lokal vorliegende de421.bsp (kein API-Call, kein
    Bot-Budget). Ein gemeinsames Zeitgrid (500 Punkte) ueber die Nacht
    (jetzt|kommender Sonnenuntergang -> Sonnenaufgang):
      - Mond: Illumination, max. Hoehe, Kulmination, >30-Grad-Fenster, Auf/Unter
      - Sonne: Fenster der astronomischen Dunkelheit (Hoehe < -18 Grad)
      - Jupiter/Saturn/Mars (Barycenter, fuer Hoehenwinkel ausreichend):
        max. Hoehe, Kulmination, >30-Grad-Fenster (planetarische Ziele)
    """
    import numpy as np
    from skyfield.api import Loader, wgs84
    from skyfield import almanac

    load = Loader(SKYFIELD_DIR)
    eph = load("de421.bsp")           # nach erstem Download rein lokal
    ts = load.timescale()
    topo = wgs84.latlon(lat, lon)

    # Nacht-Fenster: laufende (nachts) oder kommende Nacht bis Sonnenaufgang
    start_t, sunrise_t = _night_window(load, eph, ts, topo)
    obs = eph["earth"] + topo
    grid = ts.tt_jd(np.linspace(start_t.tt, sunrise_t.tt, 500))

    def alt_series(body):
        return obs.at(grid).observe(body).apparent().altaz()[0].degrees

    def window_above(alt: "np.ndarray", limit: float, g=None):
        g = grid if g is None else g
        above = np.nonzero(alt >= limit)[0]
        if len(above) == 0:
            return None
        i0, i1 = above[0], above[-1]

        def interp(i_a, i_b):
            a0, a1 = alt[i_a], alt[i_b]
            if a1 == a0:
                return g[i_b]
            f = (limit - a0) / (a1 - a0)
            return ts.tt_jd(g[i_a].tt + f * (g[i_b].tt - g[i_a].tt))

        t0 = interp(i0 - 1, i0) if i0 > 0 else g[i0]
        t1 = interp(i1, i1 + 1) if i1 + 1 < len(alt) else g[i1]
        return f"{_fmt_hm(t0)}-{_fmt_hm(t1)}"

    def body_stats(body, limit=MOON_MIN_ALT):
        alt = alt_series(body)
        imax = int(np.argmax(alt))
        return {"max_alt": round(float(alt[imax]), 1),
                "culm": _fmt_hm(grid[imax]),
                "window": window_above(alt, limit)}

    # --- Mond ---
    moon = body_stats(eph["moon"])
    if moon["max_alt"] < 0:
        # Mond steht die ganze Nacht unter dem Horizont (Sommer-Sichel):
        # keine Kulmination anzeigen statt verwirrender Negativ-Hoehe
        moon["culm"] = None
        moon["window"] = None
    t_mid = ts.tt_jd((start_t.tt + sunrise_t.tt) / 2)
    moon["illum"] = round(
        100 * almanac.fraction_illuminated(eph, "moon", t_mid), 0)
    rise_s = set_s = None
    moon_f = almanac.risings_and_settings(eph, eph["moon"], topo)
    t_m, y_m = almanac.find_discrete(start_t, sunrise_t, moon_f)
    for t, y in zip(t_m, y_m):
        if y == 1 and rise_s is None:
            rise_s = _fmt_hm(t)
        elif y == 0 and set_s is None:
            set_s = _fmt_hm(t)
    moon.update({"rise": rise_s, "set": set_s})

    # --- Astronomische Dunkelheit (Sonne < -18 Grad), bis zu 3 Naechte ---
    # Grid nach LINKS erweitert (start - 6 h): In einer laufenden Nacht
    # begann die Dunkelheit VOR 'start' - ohne Erweiterung wuerde das
    # Fenster beim 'jetzt' abgeschnitten statt beim wahren Eintritt
    # interpoliert. Multi-Intervall: ein Fenster je Nacht (bis 3).
    sun_grid = ts.tt_jd(np.linspace(start_t.tt - 0.25, start_t.tt + 3.3, 2000))
    sun_alt = obs.at(sun_grid).observe(eph["sun"]).apparent().altaz()[0].degrees
    neg = -sun_alt

    def _seg_window(i0, i1):
        def interp(i_a, i_b):
            a0, a1 = neg[i_a], neg[i_b]
            if a1 == a0:
                return sun_grid[i_b]
            f = (18.0 - a0) / (a1 - a0)
            return ts.tt_jd(sun_grid[i_a].tt + f * (sun_grid[i_b].tt - sun_grid[i_a].tt))
        t0 = interp(i0 - 1, i0) if i0 > 0 else sun_grid[i0]
        t1 = interp(i1, i1 + 1) if i1 + 1 < len(neg) else sun_grid[i1]
        return f"{_fmt_hm(t0)}-{_fmt_hm(t1)}"

    above = np.nonzero(neg >= 18.0)[0]
    dark_windows = []
    if len(above):
        seg = [above[0]]
        for i in above[1:]:
            if i == seg[-1] + 1:
                seg.append(i)
            else:
                dark_windows.append(_seg_window(seg[0], seg[-1]))
                seg = [i]
        dark_windows.append(_seg_window(seg[0], seg[-1]))
    dark = dark_windows[0] if dark_windows else None

    # --- Planeten (de421: Barycenter genuegt fuer Hoehenwinkel) ---
    planets = {}
    for pl in ("jupiter", "saturn", "mars"):
        try:
            planets[pl] = body_stats(eph[pl + " barycenter"])
        except Exception as e:
            log.warning("[Nacht] %s nicht berechenbar: %s", pl, type(e).__name__)

    moon.update({"dark": dark, "dark_windows": dark_windows[:3],
                 "planets": planets,
                 "night": f"{_fmt_hm(start_t)}-{_fmt_hm(sunrise_t)}"})
    return moon


MOON_CACHE_TTL_H = 6  # Fix 17.08.: datumsbasierter Cache alone reichte nicht -
                      # ein um 00:30 (laufende alte Nacht) berechneter Eintrag
                      # blieb sonst bis abends gueltig und lieferte um 20:30
                      # die VORHERIGE Nacht fuer die kommende. 6h TTL deckt ab.


def moon_cached(lat: float, lon: float) -> Optional[dict]:
    """compute_moon() mit Tages-Cache pro Standort. Key ist versioniert
    (v2), damit Cache-Eintraege ohne dunkel/planets nach einem Upgrade
    einmalig neu berechnet werden. Zusaetzlich TTL 6 h: das Kalenderdatum
    allein trennt 'letzte' und 'kommende' Nacht nicht sauber."""
    cache = {}
    try:
        with open(MOON_CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        pass
    key = f"v2|{datetime.now():%Y-%m-%d}|{lat:.3f}|{lon:.3f}"
    if key in cache:
        entry = cache[key]
        try:
            calc = datetime.fromisoformat(entry["calc_ts"])
            if (datetime.now() - calc).total_seconds() < MOON_CACHE_TTL_H * 3600:
                return entry
        except Exception:
            pass  # alter Eintrag ohne calc_ts -> neu berechnen
    try:
        data = compute_moon(lat, lon)
    except Exception as e:
        log.warning("[Mond] Berechnung fehlgeschlagen: %s", type(e).__name__)
        log.debug("[Mond] Traceback:\n%s", traceback.format_exc())
        return None
    data["calc_ts"] = datetime.now().isoformat(timespec="seconds")
    cache[key] = data
    # Cache alt halten: nur Eintraege der letzten 3 Tage
    cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    cache = {k: v for k, v in cache.items() if k.split("|")[1] >= cutoff}
    try:
        with open(MOON_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=1)
    except Exception as e:
        log.warning("[Mond] Cache-Schreiben fehlgeschlagen: %s", e)
    return data


def attach_moon(rep: SiteReport):
    """Astro-Felder an einen Report haengen (Mond, Dunkelheit, Planeten).
    Berechnet danach auch den Beschlags-Score (braucht Radar- und Nachtwerte)."""
    if rep.lat is None or rep.lon is None:
        return
    m = moon_cached(rep.lat, rep.lon)
    if m:
        rep.moon_illum = m["illum"]
        rep.moon_max_alt = m["max_alt"]
        rep.moon_culm = m["culm"]
        rep.moon_window = m["window"] if m["window"] else "nie >30°"
        rep.moon_rise = m["rise"]
        rep.moon_set = m["set"]
        rep.dark_window = m.get("dark")
        rep.dark_windows = m.get("dark_windows") or ([m["dark"]] if m.get("dark") else None)
        rep.planets = m.get("planets")
    rep.compute_dew_risk()


# ---------------------------------------------------------------------------
# Vorausschau (Forecast): stündliche Reihe bis Sonnenaufgang + Golden Window
# ---------------------------------------------------------------------------
# Latest-wins in ~/.astro_crawler_forecast.json (keine DB - die Kalibrierungs-
# historie lebt in den crawls-Zeilen, eine stündliche Vollhistorie waere bei
# 30-Min-Takt reine Redundanz). Kombiniert wird nur, was die Quellen eh schon
# liefern: ClearOutside/Open-Meteo (Wolken-Schichten stundeweise), Meteoblue
# (Seeing/Jet stundeweise), BrightSky-Nachtfenster (Wind/Tau/Regen stundeweise),
# skyfield (Dunkelheit/Mond als Rahmenbedingung). Null zusaetzliche Requests.

def _hh_in_window(hhmm: str, window: Optional[str]) -> bool:
    """Liegt 'HH:MM' im Fenster 'HH:MM-HH:MM' (auch über Mitternacht)?"""
    if not window or "-" not in window:
        return False
    try:
        s, e = window.split("-")
        sh, sm = map(int, s.split(":"))
        eh, em = map(int, e.split(":"))
        h, m = map(int, hhmm.split(":"))
        t, a, b = h * 60 + m, sh * 60 + sm, eh * 60 + em
        return a <= t <= b if a <= b else (t >= a or t <= b)
    except Exception:
        return False


def _hour_score(hour: dict, profile: str) -> tuple[bool, list]:
    """Bewertet eine Stunde nach den Ampel-/Rating-Schwellen (profilabhaengig).
    Liefert (ok, reasons) - reasons beschreiben, WARUM die Stunde gut ist."""
    reasons = []
    c, s = hour.get("clouds"), hour.get("seeing")
    jet, tau, wind = hour.get("jet"), hour.get("tau"), hour.get("wind")
    prob, mm = hour.get("rain"), hour.get("precip")

    if profile == "planet":
        if not hour.get("dark"):
            pass  # Planeten gehen auch in der Daemmerung - kein K.o.
        if s is not None and s > 2.0:
            return False, ["Seeing >2\""]
        if s is not None and s <= 1.5:
            reasons.append(f"Seeing {s:.1f}\"")
        if c is not None and c > 50:
            return False, [f"Wolken {c:.0f}%"]
        if c is not None and c <= 30:
            reasons.append(f"Wolken {c:.0f}%")
    else:  # DSO
        if not hour.get("dark"):
            return False, ["hell"]  # Rahmenbedingung: astron. Dunkelheit
        if c is not None and c <= 20:
            reasons.append(f"Wolken {c:.0f}%")
        if s is not None and s <= 1.0:
            reasons.append(f"Seeing {s:.1f}\"")
        if hour.get("moon_up") and (hour.get("moon_illum") or 0) > 60:
            return False, ["heller Mond hoch"]
        if tau is not None and tau >= 6:
            reasons.append(f"Tau {tau:.0f}K")
    # gemeinsame K.o.-Kriterien
    if profile == "dso" and c is not None and c > 40:
        return False, [f"Wolken {c:.0f}%"]
    if profile == "dso" and s is not None and s > 3.0:
        return False, ["Seeing >3\""]
    if jet is not None and jet > 30:
        return False, [f"Jetstream {jet:.0f}"]
    if tau is not None and tau < 3:
        return False, ["Beschlagrisiko"]
    if wind is not None and wind > 30:
        return False, [f"Wind {wind:.0f}"]
    if prob is not None and prob > 30:
        return False, [f"Regen {prob:.0f}%"]
    if mm is not None and mm > 0.1:
        return False, ["Niederschlag"]
    if not reasons:
        reasons.append("dunkel")
    return True, reasons


def build_forecast(rep: SiteReport, profile: str = "dso"):
    """Reihen kombinieren, Stunden bewerten und als JSON ablegen.

    Horizont: 3 Naechte (20.08. bestätigt). Wolken: Nacht 1 aus ClearOutside
    (feiner), Naechte 2+3 aus Open-Meteo (immer abgefragt); Boden aus dem
    56-h-BrightSky-Nachtfenster; Seeing/Jet aus Meteoblue, stundengenau null
    ab seeing_horizon (Meteoblue liefert ohne Abo heute + 2 Folgetage).
    golden_windows: Liste je Nacht; 'golden' (kompatibel) = bestes Fenster.
    Zusätzlich forecast_log-Insert (nur lead <= 48 h) für die spaetere
    Prognosegüte-Analyse."""
    try:
        # Reihen-Keys sind naive lokale Stunden (Europe/Berlin) - 'now' ebenso
        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        co = {e["ts"][:13]: e for e in (rep.fc_clouds or [])}
        om = {e["ts"][:13]: e for e in (rep.fc_clouds_om or [])}
        seeing = {e["ts"][:13]: e for e in (rep.fc_seeing or [])}
        ground = {}
        for e in (rep.fc_ground or []):
            try:
                local = datetime.fromisoformat(e["ts"]).astimezone(_berlin())
                ground[local.strftime("%Y-%m-%dT%H")] = e
            except Exception:
                continue
        dark_windows = rep.dark_windows or []
        moon_win = rep.moon_window if rep.moon_window and "nie" not in str(
            rep.moon_window) else None

        # Seeing-Horizont: letzter Meteoblue-Stundenwert + 1 h
        seeing_horizon = None
        if seeing:
            last_key = sorted(seeing)[-1]
            seeing_horizon = (datetime.strptime(last_key, "%Y-%m-%dT%H")
                              + timedelta(hours=1)).isoformat(timespec="minutes")

        series = []
        for k in sorted(set(co) | set(om) | set(seeing) | set(ground)):
            dt_h = datetime.strptime(k, "%Y-%m-%dT%H")
            if dt_h < now:
                continue
            hh = dt_h.hour
            # Raster: Nachtstunden 20:00-06:59 (7 mit Puffer fuer Sommer-Aufgang)
            if not (hh >= 20 or hh < 7):
                continue
            hhmm = dt_h.strftime("%H:%M")
            cl, omc = co.get(k), om.get(k)
            se, gr = seeing.get(k), ground.get(k)
            if cl and cl.get("total") is not None:
                clouds_v, src = cl["total"], "clearoutside"
                rain_v = cl.get("rain")
            elif omc and omc.get("total") is not None:
                clouds_v, src = omc["total"], "open_meteo"
                rain_v = omc.get("rain")
            elif gr and gr.get("cloud") is not None:
                clouds_v, src = gr["cloud"], "brightsky"
                rain_v = gr.get("prob")
            else:
                clouds_v, rain_v, src = None, None, "n/a"
            # Nacht 1: CO-Regen fiel evtl. weg - OM/BrightSky nachreichen
            if rain_v is None:
                rain_v = (omc or {}).get("rain") if omc else None
            if rain_v is None and gr:
                rain_v = gr.get("prob")
            beyond = bool(seeing_horizon and k + ":00" >= seeing_horizon)
            hour = {
                "ts": k + ":00",
                "hhmm": hhmm,
                # Nacht-Datum: Stunden >= 20 gehoeren zum Abend-Datum,
                # Stunden < 7 zum Vortag
                "night": (dt_h if hh >= 20 else dt_h - timedelta(days=1)
                          ).strftime("%Y-%m-%d"),
                "clouds": clouds_v, "src": src,
                "lmh": [((cl or omc) or {}).get(x)
                        for x in ("low", "mid", "high")],
                "seeing": (se or {}).get("seeing") if se and not beyond else None,
                "jet": (se or {}).get("jet") if se and not beyond else None,
                "tau": gr.get("tau") if gr else None,
                "wind": gr.get("wind") if gr else None,
                "rain": rain_v,
                "precip": gr.get("precip") if gr else None,
                "dark": any(_hh_in_window(hhmm, w) for w in dark_windows),
                "moon_up": _hh_in_window(hhmm, moon_win) if moon_win else False,
                "moon_illum": rep.moon_illum,
                "beyond_seeing": beyond,
            }
            hour["ok"], hour["reasons"] = _hour_score(hour, profile)
            series.append(hour)
        if not series:
            return

        # Golden Windows: je Nacht das laengste 'ok'-Segment (min. 1 h Raster)
        golden_windows = []
        for night in sorted({h["night"] for h in series}):
            hseries = [h for h in series if h["night"] == night]
            best = (0, None, None)
            i = 0
            while i < len(hseries):
                if hseries[i]["ok"]:
                    j = i
                    while j < len(hseries) and hseries[j]["ok"]:
                        j += 1
                    if j - i > best[0]:
                        best = (j - i, hseries[i]["ts"], hseries[j - 1]["ts"])
                    i = j
                else:
                    i += 1
            if best[1]:
                win = [h for h in hseries if best[1] <= h["ts"] <= best[2]]
                counts = {}
                for h in win:
                    for r in h["reasons"]:
                        counts[r] = counts.get(r, 0) + 1
                top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
                golden_windows.append({
                    "night": night,
                    "start": datetime.fromisoformat(best[1]).strftime("%H:%M"),
                    "end": (datetime.fromisoformat(best[2])
                            + timedelta(hours=1)).strftime("%H:%M"),
                    "hours": best[0],
                    "reasons": [f"{r} ({n}h)" for r, n in top],
                })
        golden = max(golden_windows, key=lambda g: g["hours"]) \
            if golden_windows else None

        data = {}
        try:
            with open(FORECAST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
        data[rep.name] = {
            "ts": datetime.now().isoformat(timespec="minutes"),
            "profile": profile,
            "dark_window": rep.dark_window,
            "dark_windows": dark_windows,
            "moon_window": rep.moon_window,
            "moon_illum": rep.moon_illum,
            "seeing_horizon": seeing_horizon,
            "sources": {"clouds": rep.clouds_source,
                        "seeing": rep.seeing_source,
                        "ground": "brightsky"},
            "series": series,
            "golden": golden,
            "golden_windows": golden_windows,
        }
        tmp = FORECAST_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, FORECAST_PATH)  # atomar: API liest nie halbfertig

        # Prognosegüte-Log: nur lead <= 48 h (Vorschlag 18.08., bestaetigt)
        db_insert_forecast_log(rep, series)

        gtxt = ", ".join(f"{g['night'][5:]}: {g['start']}-{g['end']}"
                         for g in golden_windows) or "keines"
        log.info("[Forecast] %s: %d h / %d Naechte, Windows: %s",
                 rep.name, len(series), len(golden_windows), gtxt)
    except Exception as e:
        log.warning("[Forecast] Aufbau fehlgeschlagen fuer %s: %s",
                    rep.name, type(e).__name__)
        log.debug("[Forecast] Traceback:\n%s", traceback.format_exc())
# ---------------------------------------------------------------------------
# SQLite-Historisierung (Rohwerte + Quellen + Mond + Session-Feedback)
# ---------------------------------------------------------------------------
# Zweck: spaeter pruefen, ob die Rating-Schwellen fuer die echten Spots
# realistisch sind, und Rohwerte gegen erlebte Bedingungen (/rate) stellen.
# clouds_source/seeing_source machen transparent, ob ein Wert von der
# Originalquelle oder aus einem Fallback stammt (wichtig fuer die Kalibrierung).

def db_init():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crawls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,             -- ISO-Format, Lokalzeit Berlin
            mode TEXT NOT NULL,           -- 'heavy' | 'radar'
            location_name TEXT NOT NULL,
            lat REAL, lon REAL,
            clouds_total INTEGER, clouds_low INTEGER,
            clouds_mid INTEGER, clouds_high INTEGER, clouds_source TEXT,
            rain_prob INTEGER,
            seeing REAL, jetstream REAL, seeing_index INTEGER, seeing_source TEXT,
            radar_status TEXT, precip_2h REAL,
            wind_speed REAL, dewpoint_spread REAL,
            moon_illum REAL, moon_max_alt REAL,
            moon_culm TEXT, moon_window TEXT,
            rating TEXT, errors TEXT,
            night_temp_min REAL, night_temp_max REAL,
            night_rh_max INTEGER, night_cloud_min INTEGER,
            wind_gusts REAL, dew_risk TEXT,
            dark_window TEXT, planets TEXT, profile TEXT
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            location_name TEXT NOT NULL,
            clouds INTEGER, seeing INTEGER, transparency INTEGER,
            crawl_id INTEGER,   -- letzter Heavy-Crawl dieser Location
            session_id INTEGER  -- offene /session (Startbedingungen), legacy: NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_start TEXT NOT NULL,
            ts_end TEXT,             -- NULL = offen (max. 14 h, danach auto-beendet)
            location_name TEXT NOT NULL,
            profile TEXT,            -- dso|planet zum Start
            crawl_id_start INTEGER   -- Heavy-Crawl = Startbedingungen
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fwhm_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,            -- Messzeitpunkt (vom Client geliefert)
            fwhm_arcsec REAL NOT NULL,
            location_name TEXT,          -- optional
            source TEXT,                 -- optional (z.B. 'sharpcape', 'asisair')
            created_at TEXT NOT NULL     -- Zeitpunkt des Sync-Eintrags
        )""")
    # Prognosegüte (18.08.): forecast_log ist strikt append-only (eine Zeile
    # je Standort/Ziel-Stunde je Heavy-Lauf, nur lead <= 48 h). Die spaetere
    # Verifikation schreibt ERGEBNISSE separat (forecast_verification), nie
    # Updates auf forecast_log - 'erledigt' = Verification-Zeile existiert.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forecast_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            target_ts TEXT NOT NULL,
            location_name TEXT NOT NULL,
            lead_hours REAL NOT NULL,
            clouds_total INTEGER, seeing REAL, jetstream REAL,
            dewpoint_spread REAL, wind_speed REAL, rain_prob INTEGER,
            source_clouds TEXT
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forecast_verification (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            forecast_log_id INTEGER NOT NULL,
            verified_at TEXT NOT NULL,
            matched INTEGER NOT NULL,    -- 1 = Ist-Werte gefunden, 0 = nicht verifizierbar (final)
            actual_clouds INTEGER, actual_seeing REAL,
            actual_wind REAL, actual_tau REAL,
            err_clouds REAL, err_seeing REAL, err_wind REAL, err_tau REAL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_flog_target "
                 "ON forecast_log(location_name, target_ts)")
    # Idempotente Migration bestaehender DBs: neue Spalten nachziehen
    have = {r[1] for r in conn.execute("PRAGMA table_info(crawls)")}
    migrations = {
        "night_temp_min": "REAL", "night_temp_max": "REAL",
        "night_rh_max": "INTEGER", "night_cloud_min": "INTEGER",
        "wind_gusts": "REAL", "dew_risk": "TEXT",
        "dark_window": "TEXT", "planets": "TEXT", "profile": "TEXT",
    }
    for col, typ in migrations.items():
        if col not in have:
            conn.execute(f"ALTER TABLE crawls ADD COLUMN {col} {typ}")
    have_fb = {r[1] for r in conn.execute("PRAGMA table_info(feedback)")}
    if "session_id" not in have_fb:
        conn.execute("ALTER TABLE feedback ADD COLUMN session_id INTEGER")
    conn.commit()
    conn.close()


SESSION_MAX_AGE_H = 14  # laengere offene Session gilt als beendet


def db_open_session(location_name: Optional[str] = None):
    """Die aktuell offene Session (juengste ohne ts_end, juenger als 14 h).
    Ueberfaellige offene Sessions werden beim Lesen materialisiert beendet
    (ts_end = ts_start + 14 h), damit nichts ewig offen bleibt."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cutoff = (datetime.now() - timedelta(hours=SESSION_MAX_AGE_H)
                  ).isoformat(timespec="seconds")
        conn.execute("UPDATE sessions SET ts_end = datetime(ts_start, '+14 hours') "
                     "WHERE ts_end IS NULL AND ts_start < ?", (cutoff,))
        q = ("SELECT id, ts_start, location_name, crawl_id_start FROM sessions "
             "WHERE ts_end IS NULL")
        args = []
        if location_name:
            q += " AND location_name = ?"
            args.append(location_name)
        q += " ORDER BY id DESC LIMIT 1"
        row = conn.execute(q, args).fetchone()
        conn.commit()
        conn.close()
        return row
    except Exception as e:
        log.warning("[DB] Session-Lookup fehlgeschlagen: %s", e)
        return None


def db_start_session(location_name: str, profile: str) -> dict:
    """Neue Session anlegen. Eine bereits offene wird automatisch beendet
    ('naechstes start ersetzt'), verknuepft mit dem juengsten Heavy-Crawl
    der Location als Startbedingungen."""
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("UPDATE sessions SET ts_end = ? WHERE ts_end IS NULL", (now,))
    crawl = conn.execute(
        "SELECT id, clouds_total, seeing, dew_risk, moon_illum FROM crawls "
        "WHERE location_name = ? AND mode = 'heavy' ORDER BY id DESC LIMIT 1",
        (location_name,)).fetchone()
    cur = conn.execute(
        "INSERT INTO sessions (ts_start, location_name, profile, crawl_id_start) "
        "VALUES (?,?,?,?)",
        (now, location_name, profile, crawl[0] if crawl else None))
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return {"session_id": sid, "crawl": crawl}


def db_end_session() -> Optional[tuple]:
    """Offene Session beenden; liefert (id, ts_start, location_name)."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT id, ts_start, location_name FROM sessions "
                       "WHERE ts_end IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        conn.execute("UPDATE sessions SET ts_end = ? WHERE id = ?",
                     (datetime.now().isoformat(timespec="seconds"), row[0]))
        conn.commit()
    conn.close()
    return row


def db_insert_forecast_log(rep: SiteReport, series: list):
    """Vorhersage-Zeilen fuer die spaetere Prognosegüte-Analyse loggen.
    Nur lead <= 48 h (bestaetigt 18.08.) - die Fernstunden sparen ein Drittel
    Volumen ohne den Anwendungsfall (1h/24h/Vorlauf-Vergleich) zu schmälern.
    Reines Zusatz-Logging: kein Einfluss auf Rating/Forecast."""
    try:
        now = datetime.now()
        created = now.isoformat(timespec="seconds")
        rows = []
        for h in series:
            try:
                target = datetime.fromisoformat(h["ts"])
            except Exception:
                continue
            lead = (target - now).total_seconds() / 3600
            if lead < 0 or lead > 48:
                continue
            rows.append((created, h["ts"], rep.name, round(lead, 2),
                         h.get("clouds"), h.get("seeing"), h.get("jet"),
                         h.get("tau"), h.get("wind"), h.get("rain"),
                         h.get("src")))
        if not rows:
            return
        conn = sqlite3.connect(DB_PATH)
        conn.executemany(
            "INSERT INTO forecast_log (created_at, target_ts, location_name, "
            "lead_hours, clouds_total, seeing, jetstream, dewpoint_spread, "
            "wind_speed, rain_prob, source_clouds) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        conn.close()
        log.info("[ForecastLog] %s: %d Zeilen (lead<=48h)", rep.name, len(rows))
    except Exception as e:
        log.warning("[ForecastLog] Insert fehlgeschlagen: %s", e)


def db_insert_crawl(rep: SiteReport, mode: str, profile: str = "dso"):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """INSERT INTO crawls (ts, mode, location_name, lat, lon,
               clouds_total, clouds_low, clouds_mid, clouds_high, clouds_source,
               rain_prob, seeing, jetstream, seeing_index, seeing_source,
               radar_status, precip_2h, wind_speed, dewpoint_spread,
               moon_illum, moon_max_alt, moon_culm, moon_window,
               rating, errors,
               night_temp_min, night_temp_max, night_rh_max, night_cloud_min,
               wind_gusts, dew_risk, dark_window, planets, profile)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.now().isoformat(timespec="seconds"), mode, rep.name,
             rep.lat, rep.lon,
             rep.clouds_total, rep.clouds_low, rep.clouds_mid, rep.clouds_high,
             rep.clouds_source, rep.rain_prob,
             rep.seeing, rep.jetstream, rep.seeing_index, rep.seeing_source,
             rep.radar_status, rep.precip_2h, rep.wind_speed, rep.dewpoint_spread,
             rep.moon_illum, rep.moon_max_alt, rep.moon_culm, rep.moon_window,
             rep.rate(profile)[0], ";".join(rep.errors) or None,
             rep.night_temp_min, rep.night_temp_max, rep.night_rh_max,
             rep.night_cloud_min, rep.wind_gusts, rep.dew_risk,
             rep.dark_window,
             json.dumps(rep.planets, ensure_ascii=False) if rep.planets else None,
             profile))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("[DB] Crawls-Insert fehlgeschlagen: %s", e)


def db_insert_feedback(location_name: str, clouds: int, seeing: int,
                       transp: int) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT id FROM crawls WHERE location_name = ? AND mode = 'heavy' "
            "ORDER BY id DESC LIMIT 1", (location_name,)).fetchone()
        crawl_id = row[0] if row else None
        # Session-Kopplung: laeuft fuer diese Location eine offene Session,
        # verknuepft sich das Rating mit deren Startbedingungen (ML-Zweck);
        # sonst Legacy-Verhalten (juester Crawl zum Rating-Zeitpunkt).
        sess = db_open_session(location_name)
        session_id = sess[0] if sess else None
        conn.execute(
            "INSERT INTO feedback (ts, location_name, clouds, seeing, "
            "transparency, crawl_id, session_id) VALUES (?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), location_name,
             clouds, seeing, transp, crawl_id, session_id))
        conn.commit()
        conn.close()
        log.info("[DB] Feedback: crawl_id=%s session_id=%s (%s)",
                 crawl_id, session_id, location_name)
        return True
    except Exception as e:
        log.warning("[DB] Feedback-Insert fehlgeschlagen: %s", e)
        return False


# ---------------------------------------------------------------------------
# Watchlist: temporaere Live-Standorte (per /watch, mit Ablaufzeit)
# ---------------------------------------------------------------------------
# Lock: Bot (Radar-Timer-Prozess) und FastAPI (astro-app.service) machen beide
# Read-Modify-Write auf derselben JSON. Der fcntl-Lock verhindert, dass ein
# gleichzeitiger Lauf Eintraege des anderen ueberschreibt.

import contextlib
import fcntl

WATCH_LOCK_PATH = os.path.expanduser("~/.astro_crawler_watchlist.lock")


@contextlib.contextmanager
def watchlist_lock():
    with open(WATCH_LOCK_PATH, "w") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def load_watchlist() -> list:
    try:
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_watchlist(entries: list):
    try:
        with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=1)
    except Exception as e:
        log.warning("[Watchlist] Schreiben fehlgeschlagen: %s", e)


def prune_watchlist() -> list:
    """Abgelaufene Einträge entfernen; gibt die entfernten zurueck."""
    with watchlist_lock():
        entries = load_watchlist()
        now = datetime.now()
        active, expired = [], []
        for e in entries:
            try:
                if datetime.fromisoformat(e["expires"]) > now:
                    active.append(e)
                else:
                    expired.append(e)
            except Exception:
                expired.append(e)
        if expired:
            save_watchlist(active)
            log.info("[Watchlist] abgelaufen entfernt: %s",
                     [e["name"] for e in expired])
    return expired


def active_locations(defaults: list) -> list:
    """Defaults + aktive Watchlist-Eintraege (einmalig pro Lauf zusammengebaut)."""
    wl = load_watchlist()
    return defaults + [
        {"name": e["name"], "lat": e["lat"], "lon": e["lon"]} for e in wl
    ]


# ---------------------------------------------------------------------------
# Orchestrierung + Dashboard + Telegram
# ---------------------------------------------------------------------------

async def crawl_location(context, loc) -> SiteReport:
    log.info("=== Location: %s (%s, %s) ===", loc["name"], loc["lat"], loc["lon"])
    rep = SiteReport(name=loc["name"], lat=loc["lat"], lon=loc["lon"])
    await asyncio.gather(
        scrape_clearoutside(context, loc["lat"], loc["lon"], rep),
        scrape_meteoblue(context, loc["lat"], loc["lon"], rep),
        scrape_radar(loc["lat"], loc["lon"], rep),
    )
    # Kaskade, falls ClearOutside keine Werte lieferte (Cloudflare-Budget):
    # Open-Meteo (mit L/M/H-Schichten) vor BrightSky (nur Total)
    await asyncio.to_thread(check_open_meteo_clouds, loc["lat"], loc["lon"], rep)
    await asyncio.to_thread(check_brightsky_clouds, loc["lat"], loc["lon"], rep)
    return rep


def build_dashboard(reports: list[SiteReport], profile: str = "dso") -> str:
    lines = [
        "=" * 78,
        f"  ASTRO-CRAWLER [{profile.upper()}]  |  {datetime.now():%d.%m.%Y %H:%M}",
        "=" * 78,
    ]
    for r in reports:
        fmt = lambda v, u: f"{v}{u}" if v is not None else "n/a"
        rating, icon = r.rate(profile)
        lines.append(
            f"[{r.name:<22}] Seeing: {fmt(r.seeing, ARCSEC)} "
            f"(Idx {r.seeing_index if r.seeing_index is not None else '-'}/5) "
            f"| Jet: {fmt(r.jetstream, 'm/s')} "
            f"| Clouds: {fmt(r.clouds_total, '%')} "
            f"(L/M/H {r.clouds_low}/{r.clouds_mid}/{r.clouds_high}) "
            f"| Rain: {fmt(r.rain_prob, '%')} "
            f"| Radar: {r.radar_status}"
        )
        # Info-Zeile: Boden, Beschlag, Mond, Dunkelheit
        wind = f"{r.wind_speed:.0f} km/h" if r.wind_speed is not None else "n/a"
        gusts = f", Böen {r.wind_gusts:.0f}" if r.wind_gusts is not None else ""
        tau = f"{r.dewpoint_spread:.1f} K" if r.dewpoint_spread is not None else "n/a"
        temp = (f"{r.night_temp_min:.0f}-{r.night_temp_max:.0f}°C"
                if r.night_temp_max is not None else "n/a")
        dew = f"Beschlag: {r.dew_risk}" if r.dew_risk else "Beschlag: n/a"
        lines.append(
            f"{'':>26}Wind: {wind}{gusts} | Tau: {tau} | Nacht: {temp} | {dew}"
        )
        lines.append(
            f"{'':>26}Mond: {_moon_line(r)} | Dunkel: {r.dark_window or 'n/a'}"
        )
        lines.append(f"{'':>26}-> RATING: {rating} ({icon})")
        if r.errors:
            lines.append(f"{'':>26}(Quellen-Fehler: {', '.join(r.errors)})")
    lines.append("=" * 78)
    return "\n".join(lines)


def send_telegram(text: str):
    """Dashboard per Telegram Bot-API versenden (PlainText, keine Parse-Probleme)."""
    if globals().get("TELEMETRY_DISABLED"):
        log.info("[Telegram] deaktiviert (--no-telegram)")
        return False
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("[Telegram] TELEGRAM_TOKEN/TELEGRAM_CHAT_ID fehlen "
                    "(~/.env) - ueberspringe Versand.")
        log.warning("[Telegram] Token/Chat-ID nicht gesetzt - überspringe Versand. "
                    "(Chat-ID mit --setup-telegram ermitteln)")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text[:4000],  # Telegram-Limit: 4096 Zeichen
    }).encode()
    try:
        req = urllib.request.Request(url, data=payload)
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
        ok = resp.get("ok", False)
        log.info("[Telegram] Versand %s", "erfolgreich" if ok else f"fehlgeschlagen: {resp}")
        return ok
    except Exception as e:
        log.error("[Telegram] Versand fehlgeschlagen: %s", e)
        log.debug("[Telegram] Traceback:\n%s", traceback.format_exc())
        return False


def telegram_setup():
    """Chat-ID ermitteln: Bot anschreiben, dann dieses Funktion laufen lassen.

    Ablauf:
      1. Dem Bot (@AstroCrawler007bot) in Telegram /start schicken
      2. python astro_crawler.py --setup-telegram
      3. Gefundene chat_id wird ins Skript geschrieben (TELEGRAM_CHAT_ID)
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        resp = json.loads(urllib.request.urlopen(url, timeout=15).read())
    except Exception as e:
        print(f"Fehler beim Kontakt der Telegram-API: {e}")
        return
    if not resp.get("ok"):
        print(f"API-Fehler: {resp}")
        return

    chat_ids = {}
    for upd in resp.get("result", []):
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat", {})
        if chat.get("id"):
            chat_ids[chat["id"]] = (chat.get("username") or chat.get("first_name") or "?")

    if not chat_ids:
        print("Keine Nachrichten gefunden.")
        print("=> Oeffne Telegram, schreibe dem Bot @AstroCrawler007bot '/start',")
        print("   und starte danach erneut: python astro_crawler.py --setup-telegram")
        return

    print("Gefundene Chats:")
    for cid, uname in chat_ids.items():
        print(f"  chat_id={cid}  ({uname})")

    # Erste gefundene chat_id in ~/.env eintragen (nie mehr in den Quellcode)
    chat_id = str(next(iter(chat_ids)))
    env_path = os.path.expanduser("~/.env")
    try:
        lines = []
        existing = {}
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if "=" in line and not line.strip().startswith("#"):
                        k, v = line.strip().split("=", 1)
                        existing[k] = v
        except FileNotFoundError:
            pass
        existing["TELEGRAM_CHAT_ID"] = chat_id
        with open(env_path, "w", encoding="utf-8") as f:
            for k, v in existing.items():
                f.write(f"{k}={v}\n")
        print(f"\nTELEGRAM_CHAT_ID={chat_id} wurde in {env_path} eingetragen.")
        print("Fertig! Test: python astro_crawler.py --no-headless")
    except Exception as e:
        print(f"Automatisches Eintragen fehlgeschlagen ({e}).")
        print(f"Trage manuell in ~/.env ein: TELEGRAM_CHAT_ID={chat_id}")


# ---------------------------------------------------------------------------
# Bot-Steuerung (Befehle empfangen via getUpdates, nur eigene Chat-ID)
# ---------------------------------------------------------------------------
# Der Radar-Timer (5 min) sammelt hier anstehende Befehle ein. Der Offset wird
# in der State-Datei persistiert, damit Befehle nie doppelt verarbeitet werden.
# /status startet on demand einen vollen Playwright-Crawl (~30-60 s) - daher
# TimeoutStartSec=300 in astro-radar.service.

HELP_TEXT = (
    "Astro-Crawler Bot (@AstroCrawler007bot)\n"
    "\n"
    "/status [lat lon] - Voller Lagecheck (Seeing/Wolken/Radar/Mond).\n"
    "   Ohne Koordinaten: alle 3 Standard-Spots. Mit (z.B. /status 49.5 8.6):\n"
    "   beliebiger Live-Standort vom Handy/Tablet.\n"
    "/spots - Schnellcheck: Radar + letztes Rating + Mond der 3 Spots (~5 s)\n"
    "/watch lat lon [h] - Live-Standort in den 5-Min-Watch aufnehmen,\n"
    "   automatische Gewitter-/Regen-Alarme. Default-Dauer 2 h, z.B. /watch 49.5 8.6 3\n"
    "/unwatch - Alle Live-Standorte entfernen\n"
    "/rate W S T - Session-Feedback, je 1-5 (schlecht..top):\n"
    "   W=Wolken S=Seeing T=Transparenz, z.B. /rate 4 3 5\n"
    "   Zaehlt auf die zuletzt aktive Location (letzter Spot/Watch-Eintrag)\n"
    "/mode dso|planet - Beobachtungsprofil schalten:\n"
    "   dso: Seeing >3.0\" no-go, Beschlag hart, Mond >60% maybe\n"
    "   planet: Seeing >2.0\" hart, Jetstream >30 m/s hart, Mond egal\n"
    "/clear [Spot|lat lon] - Clear-Sky-Alarm: push, wenn <20% Wolken (2 h)\n"
    "   UND astronomische Nacht; danach automatisch deaktiviert\n"
    "/session start|end - Session-Snapshot: /rate wird an die START-\n"
    "   Bedingungen gekoppelt (nicht an die Lage beim Eintippen)\n"
    "/track M81 - Meridian-Flip-Reminder: pusht 15-20 Min vor der\n"
    "   Kulmination (alle M1-M110, Standort = zuletzt aktiv)\n"
    "/help - Diese Hilfe\n"
    "\n"
    "Alarme kommen automatisch: Gewitter, Regen, Entwarnung, Rating-Wechsel."
)


async def cmd_status(args) -> str:
    if len(args) >= 2:
        try:
            lat, lon = float(args[0]), float(args[1])
        except ValueError:
            return "Koordinaten nicht lesbar. Beispiel: /status 50.0000006 8.0000006"
        locations = [{"name": f"Live {lat:.4f}/{lon:.4f}", "lat": lat, "lon": lon}]
    else:
        locations = active_locations(DEFAULT_LOCATIONS)
    reports = await run_cycle(locations, headless=True, send_dashboard=False)
    return build_dashboard(reports)


async def cmd_spots() -> str:
    """Leichtgewichtig: Radar-Check + letzte Ratings + Mond, kein Playwright."""
    state = load_state()
    lines = [f"ASTRO-CRAWLER Schnellcheck  {datetime.now():%d.%m. %H:%M}"]
    for loc in DEFAULT_LOCATIONS:
        rep = SiteReport(name=loc["name"], lat=loc["lat"], lon=loc["lon"])
        await scrape_radar(loc["lat"], loc["lon"], rep)
        attach_moon(rep)
        rating = state.get("ratings", {}).get(loc["name"], "?")
        lines.append(
            f"[{loc['name']}] Radar: {rep.radar_status} | Rating: {rating}\n"
            f"  Mond: {_moon_line(rep)}"
        )
    wl = load_watchlist()
    if wl:
        lines.append(f"Watch aktiv: {len(wl)} Live-Standort/-orte")
    return "\n".join(lines)


def _moon_line(rep: SiteReport) -> str:
    if rep.moon_illum is None:
        return "n/a (skyfield nicht verfuegbar?)"
    return (f"{rep.moon_illum:.0f}% | Kulm. {rep.moon_culm} ({rep.moon_max_alt}°) "
            f"| >30°: {rep.moon_window or 'nie'}")


async def cmd_watch(args) -> str:
    if len(args) < 2:
        return "Beispiel: /watch 50.0000006 8.0000006 3   (Dauer in Stunden, Default 2)"
    try:
        lat, lon = float(args[0]), float(args[1])
    except ValueError:
        return "Koordinaten nicht lesbar. Beispiel: /watch 50.0000006 8.0000006"
    hours = 2.0
    if len(args) >= 3:
        m = re.match(r"^(\d+(?:\.\d+)?)\s*(h|hour|stunden)?$", args[2], re.I)
        if not m:
            return f"Dauer '{args[2]}' nicht lesbar (z.B. '3' oder '90m' -> nutze Stunden)."
        hours = float(m.group(1))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return "Koordinaten ausserhalb des Erdkoordinaten-Bereichs."

    name = f"Live {lat:.4f}/{lon:.4f}"
    with watchlist_lock():
        entries = [e for e in load_watchlist()
                   if abs(e["lat"] - lat) > 0.01 or abs(e["lon"] - lon) > 0.01]
        entries.append({"name": name, "lat": lat, "lon": lon,
                        "expires": (datetime.now() + timedelta(hours=hours)).isoformat()})
        save_watchlist(entries)

    # Sofort-Radar + Mond fuer die Antwort
    rep = SiteReport(name=name, lat=lat, lon=lon)
    await scrape_radar(lat, lon, rep)
    attach_moon(rep)
    state = load_state()
    state["last_location"] = name
    save_state(state)
    return (f"Watch aktiv: {name} fuer {hours:g} h\n"
            f"Radar jetzt: {rep.radar_status} | Niederschlag 2h: "
            f"{rep.precip_2h if rep.precip_2h is not None else 'n/a'} mm\n"
            f"Mond: {_moon_line(rep)}")


def cmd_unwatch() -> str:
    with watchlist_lock():
        n = len(load_watchlist())
        save_watchlist([])
    return f"{n} Live-Standort/-orte entfernt. Nur die 3 Standard-Spots bleiben aktiv."


def cmd_rate(args, state) -> str:
    if len(args) != 3 or not all(a.isdigit() and 1 <= int(a) <= 5 for a in args):
        return ("Format: /rate W S T  (je 1-5)\n"
                "W=Wolken  S=Seeing  T=Transparenz, z.B. /rate 4 3 5")
    loc = state.get("last_location")
    if not loc:
        return ("Keine zuletzt aktive Location bekannt - zuerst /watch oder "
                "/status mit Koordinaten nutzen.")
    c, s, t = int(args[0]), int(args[1]), int(args[2])
    ok = db_insert_feedback(loc, c, s, t)
    if ok:
        return (f"Feedback gespeichert: {loc}\n"
                f"Wolken={c}/5  Seeing={s}/5  Transparenz={t}/5")
    return "Feedback konnte nicht gespeichert werden (DB-Fehler, siehe Log)."


async def cmd_clear(args, state) -> str:
    """/clear [lat lon] oder /clear [spot_name]: Clear-Sky-Watch setzen."""
    loc = None
    if len(args) >= 2:
        try:
            lat, lon = float(args[0]), float(args[1])
        except ValueError:
            return "Koordinaten nicht lesbar. Beispiel: /clear 50.0000006 8.0000006"
        loc = {"name": f"Live {lat:.4f}/{lon:.4f}", "lat": lat, "lon": lon}
    elif args:
        needle = " ".join(args).lower()
        cands = ac_all_locations()
        for c in cands:
            if needle in c["name"].lower():
                loc = c
                break
        if not loc:
            return (f"Kein Standort passt zu '{needle}'.\n"
                    f"Verfuegbar: {', '.join(c['name'] for c in cands)}\n"
                    f"Oder Koordinaten: /clear 50.0000006 8.0000006")
    else:
        return ("Format: /clear [Standort] oder /clear lat lon\n"
                "Beispiel: /clear weinheim  |  /clear 50.0000006 8.0000006\n"
                "Push kommt, wenn Bewölkung <20% (2 h) UND astronomische Nacht.")

    state["clear_watch"] = {"name": loc["name"], "lat": loc["lat"],
                            "lon": loc["lon"],
                            "set": datetime.now().isoformat(timespec="seconds")}
    # Sofort-Auskunft: aktuelle Bewölkung + Dunkelheitsfenster
    rep = SiteReport(name=loc["name"], lat=loc["lat"], lon=loc["lon"])
    await scrape_radar(loc["lat"], loc["lon"], rep)
    attach_moon(rep)
    clouds = [c for c in (rep.clouds_2h or []) if c is not None]
    return (f"Clear-Sky-Watch aktiv für {loc['name']} (max. 24 h).\n"
            f"Jetzt: max. {max(clouds) if clouds else 'n/a'}% Wolken in 2 h | "
            f"Dunkel: {rep.dark_window or 'heute keine echte Nacht'}\n"
            f"Ich pushe, sobald <20% UND astronomische Nacht gleichzeitig.")


def ac_all_locations():
    """Default-Standorte + aktive Watchlist (fuer Namens-Matching)."""
    return DEFAULT_LOCATIONS + [
        {"name": e["name"], "lat": e["lat"], "lon": e["lon"]}
        for e in load_watchlist()]


# ---------------------------------------------------------------------------
# Meridian-Flip-Reminder (/track): lokale Messier-Katalog + skyfield-Transit
# ---------------------------------------------------------------------------

MESSIER_CSV = os.path.expanduser("~/messier.csv")


def load_messier() -> dict:
    """Statischer Katalog (110 Objekte, J2000): 'M81' -> (Name, RA h, Dec °, Typ).
    Aufgebaut aus der Wikipedia-Messier-Tabelle, Stichproben-verifiziert."""
    cat = {}
    try:
        with open(MESSIER_CSV, "r", encoding="utf-8") as f:
            next(f)  # Header
            for line in f:
                p = line.strip().split(";")
                if len(p) >= 5:
                    cat[p[0].lower()] = (p[1], float(p[3]), float(p[4]), p[2])
    except Exception as e:
        log.warning("[Track] Messier-Katalog nicht lesbar: %s", e)
    return cat


def compute_transit(ra_hours: float, dec_degrees: float, lat: float, lon: float):
    """Naechste OBERE Kulmination (Meridiandurchgang, HA=0) eines Objekts.

    Sucht ueber 40 h nach echten lokalen Maxima und nimmt das erste, das
    hoeher als (global_max - 5 Grad) liegt - das trennt obere von unterer
    Kulmination bei zirkumpolaren Objekten (M81 im August: oben 70° am Tag,
    unten 28° nachts). Liefert (datetime Berlin, Hoehe°, in_nacht: bool).
    in_nacht=True nur, wenn der Durchgang VOR dem naechsten Sonnenaufgang
    liegt - sonst gibt es heute Nacht keinen flip-relevanten Meridianwechsel.
    """
    import numpy as np
    from skyfield.api import Loader, Star, wgs84

    load = Loader(SKYFIELD_DIR)
    eph = load("de421.bsp")
    ts = load.timescale()
    topo = wgs84.latlon(lat, lon)
    start_t, sunrise_t = _night_window(load, eph, ts, topo)

    star = Star(ra_hours=ra_hours, dec_degrees=dec_degrees)
    obs = eph["earth"] + topo
    grid = ts.tt_jd(np.linspace(start_t.tt, start_t.tt + 40.0 / 24.0, 2000))
    alt = obs.at(grid).observe(star).apparent().altaz()[0].degrees

    # lokale Maxima
    cands = [i for i in range(1, len(alt) - 1)
             if alt[i] >= alt[i - 1] and alt[i] >= alt[i + 1]]
    if not cands:
        return None, None, False
    gmax = float(np.max(alt))
    upper = next((i for i in cands if alt[i] > gmax - 5.0), None)
    if upper is None:
        return None, None, False
    transit_dt = grid[upper].utc_datetime().replace(tzinfo=timezone.utc
                                                    ).astimezone(_berlin())
    in_night = grid[upper].tt <= sunrise_t.tt
    return transit_dt, float(alt[upper]), in_night


def cmd_track(args, state) -> str:
    """/track [Objekt] - Meridian-Flip-Reminder 15-20 Min vor der Kulmination."""
    if not args:
        cat = load_messier()
        return ("Format: /track M81   (alle 110 Messier-Objekte)\n"
                f"Katalog geladen: {len(cat)} Objekte.")
    key = args[0].lower().lstrip("m").lstrip()
    if not key.isdigit():
        return f"'{args[0]}' ist keine Messier-Nummer. Beispiel: /track M81"
    cat = load_messier()
    entry = cat.get("m" + key)
    if not entry:
        return f"M{key} ist nicht im Katalog (M1-M110)."
    disp, ra, dec, typ = entry

    loc_name = state.get("last_location") or DEFAULT_LOCATIONS[0]["name"]
    loc = next((l for l in ac_all_locations() if l["name"] == loc_name),
               DEFAULT_LOCATIONS[0])
    try:
        transit_dt, max_alt, in_night = compute_transit(
            ra, dec, loc["lat"], loc["lon"])
    except Exception as e:
        log.warning("[Track] Transit-Berechnung fehlgeschlagen: %s", e)
        return "Transit-Berechnung fehlgeschlagen (siehe Log)."
    if transit_dt is None:
        return (f"{disp}: keine obere Kulmination in den naechsten 40 h "
                f"berechenbar - kein Watch gesetzt.")
    if max_alt < 10:
        return (f"{disp} ({typ}): kulminiert nur bei {max_alt:.0f}° Hoehe - "
                f"zu tief fuer sinnvolles Tracking, kein Watch gesetzt.")
    if not in_night:
        return (f"{disp} ({typ}): naechster Meridiandurchgang "
                f"{transit_dt:%a %H:%M} ({max_alt:.0f}°) - aber TAGSUEBER.\n"
                f"Heute Nacht gibt es keinen flip-relevanten Durchgang "
                f"(Objekt ggf. zirkumpolar tief bzw. kulminiert am Tage).")

    now = datetime.now(_berlin())
    mins = (transit_dt - now).total_seconds() / 60
    state["track_watch"] = {
        "obj": f"M{key}", "name": disp, "type": typ,
        "transit": transit_dt.isoformat(),
        "max_alt": round(max_alt, 1), "loc": loc["name"],
    }
    return (f"Track aktiv: {disp} ({typ})\n"
            f"Standort: {loc['name']}\n"
            f"Kulmination heute Nacht {transit_dt:%H:%M} ({max_alt:.0f}° Höhe), "
            f"in {int(mins // 60)}h {int(mins % 60):02d}min\n"
            f"Ich pushe 15-20 Min vorher (Meridian-Flip gleich fällig).")


def check_track_alert():
    """Im Radar-Takt: Push, wenn Kulmination in 0-20 Min naht; danach Watch weg.
    Verpasste (laenger vorbei) Watches werden still aufgeraeumt."""
    state = load_state()
    tw = state.get("track_watch")
    if not tw:
        return
    try:
        transit = datetime.fromisoformat(tw["transit"])
    except Exception:
        del state["track_watch"]; save_state(state); return
    now = datetime.now(_berlin())
    delta_min = (transit - now).total_seconds() / 60
    if 0 <= delta_min <= 20:
        del state["track_watch"]
        save_state(state)
        log.info("[Track] Flip-Push: %s kulminiert in %.0f min", tw["obj"], delta_min)
        send_telegram(
            f"🔭 {tw['obj']} {tw['name']} erreicht in ~{delta_min:.0f} Min den "
            f"Meridian ({transit:%H:%M}, max {tw['max_alt']:.0f}° Hoehe).\n"
            f"gleich flippen - Objekt wechselt die Seite! [{tw['loc']}]")
    elif delta_min < -30:
        del state["track_watch"]
        save_state(state)
        log.info("[Track] Watch verpasst (%s) - aufgeraeumt", tw["obj"])


def cmd_session(args, state) -> str:
    """/session start|end|status - Startbedingungen-Snapshot fuer /rate."""
    sub = args[0].lower() if args else "status"
    if sub == "start":
        loc = state.get("last_location")
        if not loc:
            return ("Keine aktive Location bekannt - zuerst /watch, /status "
                    "mit Koordinaten nutzen oder einen Spot crawlen lassen.")
        profile = state.get("profile", "dso")
        res = db_start_session(loc, profile)
        crawl = res["crawl"]
        sid = res["session_id"]
        if crawl:
            info = (f"Startbedingungen (Heavy-Crawl #{crawl[0]}): "
                    f"Wolken {crawl[1]}%, Seeing {crawl[2]}\", "
                    f"Beschlag {crawl[3] or 'n/a'}, Mond {crawl[4] or 'n/a'}%")
        else:
            info = "Achtung: kein Heavy-Crawl fuer diese Location vorhanden."
        return (f"Session #{sid} gestartet: {loc} [{profile}]\n{info}\n"
                f"Dein /rate wird jetzt an DIESE Startbedingungen gekoppelt.\n"
                f"/session end beendet sie (optional - naechstes start "
                f"ersetzt automatisch, Auto-Ablauf nach 14 h).")

    if sub == "end":
        row = db_end_session()
        if not row:
            return "Keine offene Session."
        try:
            dur = datetime.now() - datetime.fromisoformat(row[1])
            mins = int(dur.total_seconds() / 60)
            dur_s = f"{mins // 60}h {mins % 60}min"
        except Exception:
            dur_s = "?"
        return f"Session #{row[0]} ({row[2]}) beendet nach {dur_s}."

    # status (auch ohne Argument)
    row = db_open_session()
    if not row:
        return ("Keine offene Session.\n/session start - neue Session mit "
                "Startbedingungen-Snapshot anlegen.")
    try:
        mins = int((datetime.now() - datetime.fromisoformat(row[1])
                    ).total_seconds() / 60)
        dur_s = f"{mins // 60}h {mins % 60}min"
    except Exception:
        dur_s = "?"
    return (f"Offene Session #{row[0]}: {row[2]} seit {row[1]} ({dur_s})\n"
            f"Startbedingungen: Heavy-Crawl #{row[3]}")


def cmd_mode(args) -> str:
    if not args or args[0].lower() not in ("dso", "planet"):
        return "Format: /mode dso   oder   /mode planet"
    p = args[0].lower()
    set_profile(p)
    if p == "planet":
        return ("Profil: PLANETARISCH\nSeeing >2.0\" hart | Jetstream >30 m/s "
                "hart | Wolken >50% no-go | Mond & Beschlag irrelevant\n"
                "Bedingung: min. ein Planet (Jupiter/Saturn/Mars) >30 Grad Hoehe.")
    return ("Profil: DSO (Triband)\nSeeing >3.0\" no-go | Beschlags-Score hart "
            "(Fangspiegel ohne Heizung) | Mond >60% maybe | "
            "Temperatur/Feuchte als Ampel.")


async def handle_command(text: str, state) -> Optional[str]:
    parts = text.split()
    cmd = parts[0].split("@")[0].lower()   # auch '/cmd@botname' erlauben
    args = parts[1:]
    log.info("[Bot] Befehl: %s %s", cmd, " ".join(args)[:60])
    if cmd == "/help" or cmd == "/start":
        return HELP_TEXT
    if cmd == "/status":
        return await cmd_status(args)
    if cmd == "/spots":
        return await cmd_spots()
    if cmd == "/watch":
        return await cmd_watch(args)
    if cmd == "/unwatch":
        return cmd_unwatch()
    if cmd == "/rate":
        return cmd_rate(args, state)
    if cmd == "/mode":
        return cmd_mode(args)
    if cmd == "/clear":
        return await cmd_clear(args, state)
    if cmd == "/session":
        return cmd_session(args, state)
    if cmd == "/track":
        return cmd_track(args, state)
    return f"Unbekannter Befehl: {cmd}\n\n{HELP_TEXT}"


async def process_telegram_commands():
    """Anstehende Befehle einsammeln (Offset persistiert, Auth via Chat-ID)."""
    state = load_state()
    offset = int(state.get("telegram_offset", 0))
    url = (f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
           f"?offset={offset + 1}&timeout=0")
    try:
        resp = http_get_json(url, timeout=15, retries=1)
    except Exception as e:
        log.warning("[Bot] getUpdates fehlgeschlagen: %s", type(e).__name__)
        return
    updates = resp.get("result", [])
    if not updates:
        return
    log.info("[Bot] %d neue Nachricht(en)", len(updates))
    for upd in updates:
        state["telegram_offset"] = max(offset, upd.get("update_id", 0))
        msg = upd.get("message") or {}
        chat = str(msg.get("chat", {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if chat != TELEGRAM_CHAT_ID:
            log.warning("[Bot] unerlaubte Chat-ID %s - Befehl ignoriert", chat)
            continue
        if not text.startswith("/"):
            continue
        try:
            reply = await handle_command(text, state)
        except Exception as e:
            log.error("[Bot] Befehl fehlgeschlagen: %s: %s", type(e).__name__, e)
            log.debug("[Bot] Traceback:\n%s", traceback.format_exc())
            reply = f"Interner Fehler ({type(e).__name__}) - siehe Log."
        if reply:
            send_telegram(reply)
    save_state(state)


def heavy_age_min(name: str) -> Optional[float]:
    """Alter des juengsten Heavy-Datensatzes einer Location in Minuten
    (None = noch nie Heavy gecrawlt). Grundlage fuer den Auto-Nachzug."""
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT MAX(ts) FROM crawls WHERE mode='heavy' AND location_name = ?",
            (name,)).fetchone()
        conn.close()
        if not row or not row[0]:
            return None
        return (datetime.now() - datetime.fromisoformat(row[0])
                ).total_seconds() / 60
    except Exception:
        return None


CATCHUP_LOCK = "/tmp/astro_catchup.lock"
CATCHUP_LOCK_STALE_SEC = 15 * 60  # aeltere Locks gelten als Crash-Ueberbleibsel


def _acquire_catchup_lock() -> bool:
    """Lock fuer den Heavy-Nachzug holen. False, wenn bereits einer laeuft
    (oder das Lock nicht geschrieben werden kann). Ein Lock aelter als
    CATCHUP_LOCK_STALE_SEC ist ein Überbleibsel nach kill/Timeout und wird
    ignoriert bzw. ersetzt."""
    try:
        if os.path.exists(CATCHUP_LOCK):
            age = time.time() - os.path.getmtime(CATCHUP_LOCK)
            if age < CATCHUP_LOCK_STALE_SEC:
                return False
            log.warning("[Auto-Heavy] verwaistes Lock (%.0f min alt) - "
                        "ignoriere/ersetze es", age / 60)
        with open(CATCHUP_LOCK, "w") as f:
            f.write(f"{os.getpid()} {datetime.now().isoformat()}")
        return True
    except OSError as e:
        log.warning("[Auto-Heavy] Lock nicht setzen moeglich: %s", e)
        return False


def _release_catchup_lock():
    """Lock entfernen - steht im finally, damit es bei Exceptions sauber ist."""
    try:
        if os.path.exists(CATCHUP_LOCK):
            os.remove(CATCHUP_LOCK)
    except OSError as e:
        log.warning("[Auto-Heavy] Lock nicht entfernbar: %s", e)


async def run_cycle(locations: list, headless: bool, send_dashboard: bool,
                    radar_only: bool = False, auto_heavy: bool = True) -> list[SiteReport]:
    """Ein kompletter Durchlauf.

    radar_only=True : NUR Radar-Saeule (DWD-Warnungen + BrightSky) - kein
                      Playwright, kein Browser, laeuft in Sekundenbruchteilen.
                      Fuer den 5-Minuten-Timer (Gewitter-Echtzeit-Alarm).
    radar_only=False: Heavy-Crawl mit frischem Browser pro Zyklus (30-Min-Timer).
    """
    if radar_only:
        reports = []
        for loc in locations:
            rep = SiteReport(name=loc["name"], lat=loc["lat"], lon=loc["lon"])
            await scrape_radar(loc["lat"], loc["lon"], rep)
            reports.append(rep)
            print(f"[{rep.name:<22}] Radar: {rep.radar_status}")
        dashboard = None
    else:
        pw, browser, context = await make_browser(headless=headless)
        try:
            reports = []
            for loc in locations:  # sequenziell -> weniger Bot-Verdacht
                reports.append(await crawl_location(context, loc))
        finally:
            await browser.close()
            await pw.stop()
    # Mond-Ephemeriden (lokal + Tages-Cache, laeuft in beiden Modi) - VOR dem
    # Dashboard-Aufbau; berechnet zugleich den Beschlags-Score
    for rep in reports:
        attach_moon(rep)
    # Aktives Beobachtungsprofil (dso|planet) steuert Rating-Schwellen
    state = load_state()
    profile = get_profile(state)
    dashboard = None
    if not radar_only:
        # Vorausschau: Reihen kombinieren + Golden Window (latest-wins JSON)
        for rep in reports:
            build_forecast(rep, profile)
        dashboard = build_dashboard(reports, profile)
        print(dashboard)

    # Zustandsvergleich + Alarmierung (Cooldowns persistieren in der Datei)
    alert = evaluate_alerts(reports, state, radar_only=radar_only,
                            profile=profile)
    if not radar_only and reports:
        # Merker fuer /rate: "zuletzt aktive Location"
        state["last_location"] = reports[-1].name
    save_state(state)

    # Historisierung: jeder Lauf landet in SQLite (Rohwerte + Mond + Rating)
    mode = "radar" if radar_only else "heavy"
    for rep in reports:
        db_insert_crawl(rep, mode, profile)

    if radar_only:
        # Meridian-Flip-Reminder: /track-Watch pruefen (Push + Auto-Aus)
        try:
            check_track_alert()
        except Exception as e:
            log.warning("[Track] Prüfung fehlgeschlagen: %s", e)

        # Clear-Sky-Alarm: /clear-Bedingung pruefen (einmaliger Push + Auto-Aus)
        try:
            check_clear_alert(reports)
        except Exception as e:
            log.warning("[Clear] Prüfung fehlgeschlagen: %s", e)

        # Wind-Eskalation: nur bei aktiver Session (Warnung >40, Abbruch >60
        # mit 2-Tick-Debounce)
        try:
            check_wind_alert(reports)
        except Exception as e:
            log.warning("[Wind] Prüfung fehlgeschlagen: %s", e)

        # 1x taeglich: Meilenstein-Check fuer /rate-Feedback (20/50 Sessions)
        try:
            check_milestones()
        except Exception as e:
            log.warning("[Milestone] Check fehlgeschlagen: %s", e)

        # Auto-Heavy-Nachzug (Fix 16.08., verschaerft: Delay + Lock):
        # - Standorte ohne frische Heavy-Daten (>60 min / nie gecrawlt)
        # - SEQUENZIELL mit 60-90 s Pause dazwischen (Cloudflare-Budget ist
        #   rollierend ~3 Requests/paar Minuten - ein Batch-Parallelstart
        #   verbrennt es sofort)
        # - Lock-Datei: laeuft noch ein Nachzug, skippt der naechste Tick nur
        #   den NACHZUG - die normalen Radar-HTTP-Calls laufen weiter
        # - try/finally raeumt das Lock auf; ein Lock aelter als 15 min gilt
        #   als Überbleibsel (kill -9 / Service-Timeout) und wird ignoriert
        if auto_heavy:
            stale = [loc for loc in locations
                     if (a := heavy_age_min(loc["name"])) is None or a > 60]
            if stale and _acquire_catchup_lock():
                try:
                    log.info("[Auto-Heavy] %d Standort(e) veraltet -> "
                             "sequenzieller Nachzug: %s", len(stale),
                             [l["name"] for l in stale])
                    for i, loc in enumerate(stale):
                        if i:
                            pause = random.uniform(60, 90)
                            log.info("[Auto-Heavy] %.0f s Pause vor '%s' "
                                     "(Cloudflare-Budget schonen)", pause,
                                     loc["name"])
                            await asyncio.sleep(pause)
                        await run_cycle([loc], headless=headless,
                                        send_dashboard=False, radar_only=False,
                                        auto_heavy=False)
                finally:
                    _release_catchup_lock()
            elif stale:
                log.info("[Auto-Heavy] %d Standort(e) veraltet, aber Nachzug "
                         "laeuft bereits (Lock) - nur Radar-HTTP in diesem Tick",
                         len(stale))

    if alert:
        mode_txt = "RADAR-ALARM" if radar_only else "ALARM"
        log.info("[Alert] Ereignis erkannt -> Telegram-Alarm wird gesendet")
        send_telegram(f"ASTRO-CRAWLER {mode_txt}\n{datetime.now():%d.%m. %H:%M}\n\n{alert}")
    elif send_dashboard and dashboard:
        send_telegram(dashboard)
    return reports


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Astro-Crawler: Go/No-Go für Teleskop-Einsätze")
    parser.add_argument("--lat", type=float, help="Ad-hoc-Koordinate (Breitengrad)")
    parser.add_argument("--lon", type=float, help="Ad-hoc-Koordinate (Längengrad)")
    parser.add_argument("--name", default="Ad-hoc Spot",
                        help="Anzeigename für --lat/--lon (Default: 'Ad-hoc Spot')")
    parser.add_argument("--no-headless", action="store_true",
                        help="Sichtbaren Browser starten (hilft bei Cloudflare)")
    parser.add_argument("--debug", action="store_true",
                        help="TRACEBACKS + HTML-Schnipsel ins Log schreiben")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Telegram-Versand deaktivieren")
    parser.add_argument("--setup-telegram", action="store_true",
                        help="Chat-ID ermitteln (Bot vorher mit /start anschreiben)")
    parser.add_argument("--radar-only", action="store_true",
                        help="Nur DWD/BrightSky pruefen (kein Browser) - "
                             "fuer den schnellen 5-Minuten-Timer")
    parser.add_argument("--watch", action="store_true",
                        help="Dauerbetrieb: Crawlt endlos im Abstand von --interval")
    parser.add_argument("--interval", type=int, default=30, metavar="MIN",
                        help="Watch-Intervall in Minuten (Default: 30)")
    parser.add_argument("--alert-only", action="store_true",
                        help="Telegram nur bei Alarmen, kein Routine-Dashboard")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.setup_telegram:
        telegram_setup()
        return 0

    if (args.lat is None) != (args.lon is None):
        parser.error("--lat und --lon müssen zusammen angegeben werden")

    db_init()  # SQLite-Schema idempotent anlegen

    if args.lat is not None:
        locations = [{"name": args.name, "lat": args.lat, "lon": args.lon}]
        log.info("Ad-hoc-Modus: %s (%s, %s)", args.name, args.lat, args.lon)
    else:
        # Radar-Modus: zuerst abgelaufene Watches entfernen (einmalige Info),
        # dann anstehende Bot-Befehle abarbeiten (koennen /watch hinzufuegen),
        # erst danach die effektive Standortliste zusammenbauen.
        if args.radar_only:
            expired = prune_watchlist()
            if expired:
                names = ", ".join(e["name"] for e in expired)
                log.info("[Watchlist] abgelaufen: %s", names)
                send_telegram(f"⏱ Watch abgelaufen: {names}\n"
                              f"(Live-Standort wieder vom Radar entfernt.)")
            await process_telegram_commands()
        locations = active_locations(DEFAULT_LOCATIONS)
        # /clear-Watch-Standort zum Radar-Takt hinzufuegen (falls aktiv)
        cw = load_state().get("clear_watch")
        if cw and not any(l["name"] == cw["name"] for l in locations):
            locations.append({"name": cw["name"], "lat": cw["lat"],
                              "lon": cw["lon"]})
        if args.radar_only and len(locations) > len(DEFAULT_LOCATIONS):
            log.info("[Watchlist] %d Live-Standort/-orte aktiv",
                     len(locations) - len(DEFAULT_LOCATIONS))

    if args.no_telegram:
        global TELEMETRY_DISABLED
        TELEMETRY_DISABLED = True

    if args.watch:
        log.info("Watch-Modus: Crawle alle %d Minuten (Strg+C beendet)", args.interval)
        while True:
            try:
                await run_cycle(locations, headless=not args.no_headless,
                                send_dashboard=not args.alert_only,
                                radar_only=args.radar_only)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("Zyklus fehlgeschlagen (%s) - naechster Versuch im "
                          "naechsten Intervall", type(e).__name__)
                log.debug("Traceback:\n%s", traceback.format_exc())
            await asyncio.sleep(args.interval * 60)

    reports = await run_cycle(locations, headless=not args.no_headless,
                              send_dashboard=not args.alert_only,
                              radar_only=args.radar_only)

    # Exit 0 = Lauf technisch OK (fuer systemd-Timer). Ein Crash (ungefangene
    # Exception) fuehrt automatisch zu Exit != 0 und markiert den Service rot.
    # Die Rating-Entscheidung geht per Telegram/Log raus, nicht per Exit-Code.
    if args.radar_only:
        n_warn = sum(1 for r in reports if "Alert" in r.radar_status)
        log.info("Radar-Lauf abgeschlossen: %d/%d Locations mit Warnung",
                 n_warn, len(reports))
    else:
        profile = get_profile(load_state())
        go_count = sum(1 for r in reports if r.rate(profile)[0] == "GO")
        log.info("Lauf abgeschlossen [%s]: %d/%d Locations mit GO",
                 profile, go_count, len(reports))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
