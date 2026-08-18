# Astro Command Center — System-Blueprint

Stand: 2026-08-15 · Host: `seriousjoke` (Ubuntu 22.04, Python 3.10) · Autor: GLM/ZCode

Dieses Dokument ist der technische Ist-Stand UND der Blueprint für Phase 2
(Equipment-spezifische Erweiterung). Teil 1 beschreibt exakt das, was deployed
ist; Teil 2 die geplanten Erweiterungen für das Quattro-150P-Setup.

---

## TEIL 1 — IST-STAND

### 1.1 Komponenten & Deployment

```
~/astro_crawler.py                 Crawler+Bot (importiert von beiden Timern)
~/.astro_crawler_state.json        Alert-State: radar{}, ratings{}, last_alert{},
                                   telegram_offset, last_location, milestone-Flags (geplant)
~/.astro_crawler_watchlist.json    Live-Standorte (name, lat, lon, expires ISO)
~/.astro_crawler_watchlist.lock    fcntl-Lockfile (Bot-Prozess <-> API-Prozess)
~/.astro_crawler.db                SQLite: crawls + feedback
~/.astro_crawler_moon.json         Mond-Tages-Cache (3 Tage Retention)
~/.skyfield/de421.bsp              JPL-Ephemeris 17 MB (einmalig, lokal)
~/astro-app/backend/               FastAPI: main.py, lpcache.py
~/astro-app/frontend/              PWA: index.html, app.js, style.css, sw.js,
                                   manifest.webmanifest, icons/, vendor/leaflet
~/astro-app/tilecache/             LP-Tiles (PNG, permanent)
```

**systemd-User-Units** (`~/.config/systemd/user/`, `loginctl enable-linger` aktiv,
läuft also ohne Login und übersteht Reboots):

| Unit | Takt | Aufruf | Zweck |
|---|---|---|---|
| `astro-crawler.timer` | `OnCalendar=*:0/30`, Persistent, RandDelay 45 s | `astro_crawler.py --alert-only` | Heavy-Crawl (Playwright) |
| `astro-radar.timer` | `OnCalendar=*:2/5`, Persistent, RandDelay 20 s | `astro_crawler.py --radar-only --alert-only` | DWD/BrightSky/skyfield + Bot-Befehle |
| `astro-app.service` | Dauerdienst | `uvicorn main:app --host 0.0.0.0 --port 8000` | API + PWA (nach Serve-Aktivierung: 127.0.0.1) |

Timer sind bewusst versetzt (:0/:30 vs. :2/:7/…), `Persistent=true` holt nach
Standby/Boot genau einen verpassten Lauf nach. Oneshot-Services mit
`TimeoutStartSec` 600 (Heavy) / 300 (Radar, wegen /status-On-Demand-Crawl).

### 1.2 Datenflussebene (ein Heavy-Zyklus)

```
run_cycle(locations = DEFAULT_LOCATIONS + Watchlist, headless)
 ├─ pro Location SEQUENZIELL (Bot-Verdacht-Minimierung), pro Location PARALLEL:
 │   ├─ scrape_clearoutside()   [Playwright, neuer Tab]
 │   ├─ scrape_meteoblue()      [Playwright, neuer Tab]
 │   └─ scrape_radar()          [asyncio.to_thread, kein Browser]
 │       ├─ check_dwd_warnings()
 │       └─ check_brightsky_ground()
 ├─ check_brightsky_clouds()    [nur falls clouds_total == None]
 ├─ attach_moon()               [skyfield, Tages-Cache]
 ├─ evaluate_alerts(reports, state, radar_only)
 ├─ db_insert_crawl(rep, 'heavy')
 └─ send_telegram(ALARM|Dashboard)
```

Radar-Zyklus identisch, aber ohne Playwright; `evaluate_alerts(radar_only=True)`
fasst `ratings{}` nie an.

### 1.3 Datenquellen & extrahierte Parameter (exakt)

**ClearOutside** — `https://clearoutside.com/forecast/{lat:.4f}/{lon:.4f}`
Playwright/Chromium (stealth: AutomationControlled-Flag aus, navigator.webdriver
undefined, realistischer UA, locale/timezone). Extraktion NICHT per CSS-Selektor,
sondern **Text-Parse des gerenderten Bodys** (die Seite nutzt keine `<table>`,
sondern div-Zeilen):

- Strategie 1: `page.inner_text("body")` + Regex
  `re.escape(Label) + r"[^\d]*((?:\s*\d{1,3}){5,})"` für Labels
  `Total Clouds` / `Low Clouds` / `Medium Clouds` / `High Clouds` /
  `Precipitation Probability`
- Strategie 2 (Fallback): XPath `//*[contains(., Label)]/following-sibling::*[1]`
- Aggregation: Maximum der ersten 5 Stundenzellen (aktuelle Stunde + 4 h)
- Vor jedem Request `asyncio.sleep(random.uniform(1.5, 4.0))`
- Cloudflare-Retry: Titel `"Just a moment"` → reload + 10 s warten (einmalig)
- Liefert: `clouds_total/low/mid/high` (%, % Sky Obscured), `rain_prob` (%)

**Meteoblue Astronomical Seeing** —
`https://www.meteoblue.com/en/weather/forecast/seeing/{abs(lat):.2f}{'N'|'S'}{abs(lon):.2f}{'E'|'W'}`
Server-seitig gerenderte Stundentabelle, geparst aus `inner_text("main")`
(Fallback `body`):

- Zeilen-Regex (MULTILINE):
  `^\s*(\d{1,2})\s+(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s+(\d{1,2}[.,]\d{1,2})\s+([1-5])\s+([1-5])\s+(\d{1,3})\s*m/s`
  → Stunde | Low% | Mid% | High% | ArcSec | Index1 | Index2 | JetStream m/s
- Tagesblöcke via `(Mon|Tue|…|Sun)[ ,]+\d{4}-\d{2}-\d{2}`
- Nacht-Fenster: heute Stunde≥now bis 23 Uhr + morgen 0–5 Uhr, max. 8 Stunden
- **Worst-Case-Aggregation** über das Fenster: `seeing = max`, `jetstream = max`,
  `seeing_index = min` (konservativ für Go/No-Go)

**DWD Geoserver WFS** (amtliche Unwetterwarnungen — Ersatz für Kachelmannwetter,
dessen Akamai headless Chrome dauerhaft blockt) —
`https://maps.dwd.de/geoserver/dwd/ows?service=WFS&version=2.0.0&request=GetFeature&typeName=dwd:Warnungen_Gemeinden&outputFormat=application/json&bbox={lon±0.20},{lat±0.15},EPSG:4326`
(~15 km Radius). Vollständige Warnpolygone (Polygon/MultiPolygon, EPSG:4326) +
**Punkt-in-Polygon-Prüfung (Ray-Casting)** ob der Standort wirklich im
Warngebiet liegt. EVENT-Mapping: `GEWITTER` → Storm; `REGEN/NIEDERSCHLAG/
SCHNEE/STARKREGEN` → Rain; alles andere (HITZE, WIND …) fürs Teleskop irrelevant
→ Clear. Liefert `radar_status`.

**Bright Sky** (DWD-Rohdaten, kein Bot-Schutz) —
`https://api.brightsky.dev/weather?lat&lon&date..last_date`:
- 2-h-Fenster: `precipitation` (Summe mm; >0,1 mm → Rain Alert, sofern keine
  stärkere DWD-Warnung vorliegt), `wind_speed` (max, km/h),
  `temperature − dew_point` (Spread min, K)
- 4-h-Fenster (nur Fallback): `cloud_cover` (max, %) → Quelle
  `brightsky_fallback`
- Läuft IMMER (auch bei aktiver Warnung), damit Wind/Tau lückenlos historisiert

**skyfield (lokal, de421.bsp)** — Nacht-Fenster = jetzt|kommender
Sonnenuntergang → Sonnenaufgang (almanac.risings_and_settings). Mond:
Illumination % (Nachtmitte), max. Höhe + **Kulminationszeit** (500-Punkt-
Höhen-Sampling, argmax), **>30°-Fenster** (lineare Interpolation an beiden
Kanten), Auf-/Untergang. Tages-Cache pro (Datum|lat|lon). Berechnung < 0,1 s.
Reines INFO-Feld — bewusst NICHT im Rating (Mond = Freund für Planetarisch,
Feind für DSO).

**Open-Meteo** (verifiziert 2026-08-15, Integration geplant):
`/v1/forecast?hourly=cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,precipitation_probability`
— key-frei, kein Bot-Schutz; künftig Schicht-Fallback vor BrightSky.
Außerdem Air-Quality-API (CAMS) für Transparenz-Proxy (Teil 2).

**Light Pollution (nur Karte)**: Lorenz-Atlas-Tiles
`djlorenz.github.io/astronomy/image_tiles/tiles2025/tile_{z}_{x}_{y}.png`
(nativ bis z6, via Backend-Proxy mit permanentem Disk-Cache), Fallback NASA GIBS
`VIIRS_Black_Marble` (bis z8).

### 1.4 Wolken-Kaskade & Cloudflare-Budget (IST + geplant)

IST: **ClearOutside → BrightSky (nur Total)**.
Befund (DB + Live-Repro): Cloudflare rollierendes Budget ≈ **3 Requests pro
wenige Minuten pro IP** — nicht „3. Standort strukturell geblockt", sondern
der letzte Call im Batch trifft das erschöpfte Budget am häufigsten (Weinheim:
4/4 Fallback, Position 1+2: 9/9 ClearOutside). Delay hilft kaum; erst
mehrere Minuten Abstand wären zuverlässig.

GEPLANT (Teil 2 bestätigt): **ClearOutside → Open-Meteo (Schichten L/M/H +
Regen-%) → BrightSky (Total)**. Open-Meteo läuft außerhalb jedes Browser-
Budgets — das Cloudflare-Risiko sinkt sogar, weil wir auf ClearOutside-Retries
verzichten können, sobald ein sauberer Sekundärer existiert.

### 1.5 Rating-Logik (implementiert, `SiteReport.rate()`)

Strengkeits-Reihenfolge (erste Übereinstimmung gewinnt):

1. `radar_status` enthält Storm/Rain → **NO-GO** 🔴
2. `clouds_total > 40 %` → **NO-GO** 🔴
3. `seeing > 2.0″` → **NO-GO** 🔴
4. Radar Unknown UND Wolken fehlen → **NO DATA** ⚪
5. `clouds_total > 20 %` → **MAYBE** 🟡
6. sonst → **GO** 🟢

Nacht-Fenster-Aggregation ist konservativ (Worst Case über bis zu 8 Nachtstunden
bzw. 4 h Wolken). Mond/Wind/Tau fließen aktuell NICHT ins Rating (nur Info).

### 1.6 Alert-Engine & Telegram-Bot

`evaluate_alerts()` — Ereignisbasiert, keine Routine-Pushes:
- **Storm-Alarm**: sofort, Cooldown-Durchbruch bei Neu/Eskalation (Rain→Storm)
- **Rain-Alarm**: Cooldown 90 min pro Standort+Art (`last_alert`-Keys)
- **Entwarnung**: Rain/Storm → Clear (einmalig, kein Cooldown)
- **Rating-Wechsel** (nur Heavy-Modus): GO→NO-GO und NO-GO→GO/MAYBE
- **Flatterschutz**: `Unknown` (Netz/API down) überschreibt NIE den letzten
  bekannten Radar-Status; Radar-Modus schreibt `ratings{}` nie

Bot `@AstroCrawler007bot` (getUpdates im Radar-Timer, Offset persistiert,
Auth: nur `TELEGRAM_CHAT_ID`):
`/status [lat lon]` on-demand Heavy-Crawl · `/spots` Schnellcheck (Radar+
Ratings+Mond) · `/watch lat lon [h]` (Default 2 h) · `/unwatch` ·
`/rate W S T` (1–5 Wolken/Seeing/Transparenz → feedback-Tabelle mit
`crawl_id`-Verknüpfung zum letzten Heavy-Crawl der Location) · `/help`

### 1.7 Backend-API, PWA, Persistenz

FastAPI (`astro-app.service`, importiert `astro_crawler` — keine Logik-Duplizierung):
- `GET /api/spots` — pro Standort: jüngster Heavy-Wert (Seeing/Wolken/Rating)
  + jüngster Lauf überhaupt (Radar/Wind/Tau, 5-min-frisch) + Mond + Alter in Min
- `GET /api/history?location&hours≤336` — Zeitreihe aus SQLite
- `GET /api/moon?lat&lon` — skyfield-Cache
- `POST /api/watch` / `DELETE /api/watch` — **unter fcntl-Lock** (gleiche
  Lockdatei wie der Bot-Prozess → keine Lost-Updates zwischen Timer und API);
  optional `X-API-Token` via Env `ASTRO_API_TOKEN`
- `GET /api/warnings` — DWD-GeoJSON (60-s-Cache; storm rot / rain blau, „other"
  gefiltert)
- `GET /api/lp-tiles/{z}/{x}/{y}` — Proxy + Disk-Cache

PWA: Leaflet 1.9.4 lokal (kein CDN), Carto-Voyager-Basemap, Rating-farbene
divIcon-Marker + Alert-Badge, Detail-Panel (alle Telegram-Werte inkl. Mond),
LP- + Warnungs-Layer (Layer-Control), **Rotlicht-Modus** (rot-auf-schwarz,
Kacheln per CSS-Filter), 60-s-Auto-Refresh, „vor X Min"+OFFLINE-Kennzeichnung,
Service Worker (Shell offline, API network-first+Cache-Fallback), `BASE_URL`
über ⚙ konfigurierbar (Capacitor-sicher), GPS-Button → `POST /api/watch`
(benötigt HTTPS → `tailscale serve`).

SQLite-Schema (Auswahl): `crawls(id, ts, mode, location_name, lat, lon,
clouds_total/low/mid/high, clouds_source, rain_prob, seeing, jetstream,
seeing_index, seeing_source, radar_status, precip_2h, wind_speed,
dewpoint_spread, moon_illum, moon_max_alt, moon_culm, moon_window, rating,
errors)` · `feedback(id, ts, location_name, clouds, seeing, transparency,
crawl_id)`.

---

## TEIL 2 — PHASE 2: EQUIPMENT-SPEZIFISCHE ERWEITERUNG

Equipment-Referenz: Skywatcher Quattro 150P f/5 (offener Newton, Fangspiegel-
Upgrade, **KEINE Tauheizung**) · EQ5 Pro SynScan · Canon EOS 600D (ungekühlt) ·
ZWO ASI662MC (ungekühlt) · ASI120MC-S Guides · ASIAir Mini + Legion 5
(SharpCap Pro) · Antlia Triband RGB Ultra, Omegon UHC, UV/IR-Cut,
variabler Mondfilter.

### 2.1 Neue Parameter — begründet am Equipment

| # | Parameter | Warum GENAU für dieses Setup | Quelle | Kosten |
|---|---|---|---|---|
| 1 | **Lufttemperatur Nachtverlauf** (max/aktuell) | 600D ungekühlt: Dark Current verdoppelt ~alle 6 K; 25–30 °C-Sommernächte → deutlich mehr Rauschen + Banding; ASI662MC (IMX462) ist hitzeverträglicher, aber nicht immun | BrightSky `temperature` — **steckt bereits in jeder Response**, nur Auswertung | 0 neue Requests |
| 2 | **Relative Luftfeuchte** (min/aktuell) | Beschlag-Vorlaufindikator am offenen Tubus; rh>90 % + kleiner Spread = akut | BrightSky `relative_humidity` — in Response | 0 |
| 3 | **Windböen** (max 2 h) | EQ5 mit 150P-Tubus = Segelfläche; Böen >35–40 km/h → Vibration, Nachführfehler, bei Guss-Gefahr | BrightSky `wind_gusts` — in Response | 0 |
| 4 | **Beschlags-Score** (abgeleitet) | Fangspiegel OHNE Heizung strahlt nach klarem Himmel aus (radiative cooling, 2–6 K unter Lufttemp) → Beschlag möglich, BEVOR Spread=0: klarer Himmel + Schwachwind + Spread<4–6 K = kritisch | Kombination: Spread × clouds_total × wind (alle vorhanden) | 0 Requests, reine Logik |
| 5 | **Astronomische Dunkelheit** (Sonne < −18°, Fenster) | DSO/Triband braucht echte Nacht; aktuelles Nacht-Fenster endet/ beginnt bei bürgerlicher Dämmerung | skyfield `almanac.dark_twilight_day` — lokal, de421 enthält die Sonne | 0 Requests |
| 6 | **Transparenz-/Aerosol-Proxy** (pm10, pm2.5, dust) | Saharastaub/Waldbrand = Streulicht: killt Kontrast trotz Triband; UHC/Breitband leidet noch stärker | Open-Meteo **Air Quality API** (CAMS-Daten, key-frei, kein Bot-Schutz) | 1 Request/Standort, 30–60-min-Takt reicht |
| 7 | **Planeten-Fenster** (Jupiter/Saturn/Mars: Höhe>30° + Bestzeit) | ASI662MC + SharpCap = Planetarisch-Setup; de421.bsp enthält Planeten-Ephemeriden — Höhe/Kulmination wie beim Mond berechenbar | skyfield, lokal | 0 Requests, nur CPU |
| 8 | (optional) AOD550 | Direktere Transparenz-Messung als PM-Proxy | CAMS/ADS API | Key nötig → NICHT empfohlen für v1 |

### 2.2 Rating-Erweiterung: Beobachtungs-Profile

Statt einer Starr-Rating zwei Profile (`/mode dso|planet`, persistiert in
State, Bot-Befehl + API-Feld + Frontend-Toggle):

**DSO (Triband) — Default:**
- Wolken >40 % → NO-GO (wie gehabt) · Seeing >3.0″ → NO-GO (Triband/DSO ist
  seeing-toleranter als das aktuelle 2.0″; Sterngröße leidet, aber die
  Flächenebene nicht)
- NEU hart: Beschlags-Score hoch → NO-GO („Fangspiegel beschlägt ohne Heizung")
- NEU: Temperatur >25 °C → MAYBE-Downgrade („600D-Rauschen hoch — kürzere
  Subs/ mehr Darks einplanen") · 18–25 → Hinweis
- Mond-Illum >60 % → MAYBE · außerhalb astronomischer Dunkelheit → „zu hell"

**PLANETARISCH/Mond:**
- Seeing ≤2.0″ hart (150 mm ≈ 1″ Beugungslimit; >2″ ist tot für Planeten)
- Jetstream >30 m/s → NO-GO · Wolken >50 % → NO-GO
- Mond/Planeten-Höhe >30° als Bedingung (Kulminations-Fenster + Planeten-
  Fenster) · Dämmerung irrelevant · Beschlag am Fangspiegel sekundär (Planeten
  ab ~30° Höhe über Dächerern)

Implementierung: `rate(profile="dso")` — Abwärtskompatibel: ohne Profil
verhält sich DSO wie heute (bis auf dokumentierte Schwellen-Anpassung Seeing).

### 2.3 Code-Strategie (Cloudflare-neutral)

1. **BrightSky-Auswertung erweitern** (`check_brightsky_ground`): Fenster von
   2 h auf Nacht-Fenster (ein Call, `last_date` = Sonnenaufgang) →
   `temperature_max`, `rh_min`, `wind_gusts_max`. Null Zusatz-Request.
2. **skyfield-Modul erweitern** (`compute_dark_window`, `compute_planets`):
   gleiche Loader/eph-Instanz wie Mond — einmal laden, alles berechnen,
   Tages-Cache. Läuft im 5-min-Radar-Timer mit (CPU ~0,2 s, gecacht ~0).
3. **Open-Meteo** als eigene Funktion `check_open_meteo(lat, lon, rep)`
   (Wolken-Schicht-Fallback + Air-Quality) via bestehendem `http_get_json`
   (Retry-Wrapper). Aufruf NUR im Heavy-Timer (30 min) — AQ ändert sich
   langsam. Kein Browser → berührt ClearOutside-Budget NICHT.
4. **DB-Migration**: `db_migrate()` mit `PRAGMA table_info`-Check +
   idempotenten `ALTER TABLE crawls ADD COLUMN temperature_max REAL, …`.
5. **Meilenstein-Alarm**: 1× täglich im Radar-Zyklus (erster Lauf nach 12 Uhr,
   `milestone_check_date`), Flags `milestone_20_sent`/`milestone_50_sent`,
   Meldung bei 20 (Halbzeit) und 50 (Modellstart GBT) bewerteten Sessions.

### 2.4 Infra

- Seit 17.08.: Git-Repo im Home (Whitelist), astro_deploy.sh
  committet jeden Deploy; Changelog unter astro-app/changelog.json.

## 2.4 Offene Infra-Punkte

- `tailscale serve` — Freischaltung im Tailnet ist erfolgt; es fehlt der
  einmalige Operator-Befehl:
  `sudo tailscale set --operator=enigma && tailscale serve --bg http://127.0.0.1:8000`
  Danach: uvicorn zurück auf 127.0.0.1 (serve = einziger externer Weg, HTTPS
  für GPS/Geolocation). HTTPS-URL: `https://seriousjoke.<tailnet>.ts.net`
- Icons austauschbar über `frontend/icons/` (PNG 192/512 + SVG), sonst nichts.
