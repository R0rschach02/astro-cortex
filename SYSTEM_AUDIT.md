# SYSTEM_AUDIT.md — Astro Command Center
**Stand:** 2026-09-21 · **Zweck:** Vorbereitung der Capacitor-Migration (native App)
**Charakter:** Read-Only-Audit — dokumentiert IST-Zustand und Zielarchitektur, ohne aktiven Code zu verändern.

---

## 1. UI/UX-Architektur (Desktop vs. Mobile-Overlay-Konzept)

### 1.1 Inventar der UI-Komponenten (IST, Desktop-first)

| Komponente | Ort | Technik | Datenquelle / Update |
|---|---|---|---|
| **Glareshield-Gauges** (5) | Top-Bar, horizontal (`gap: 2vw`) | SVG-Generator `buildGauges()`: Bezel-Ring, 4 Schlitzschrauben (45/135/225/315°), Major-Ticks mit Zahlen + Minor-Ticks, Nadeln mit Gegengewicht-Schaft; Zentrum (50,50), Rotation **nur** als SVG-Attribut `rotate(W 50 50)` — nie CSS-transform (Lesson: dezentrierte Nadeln) | `updateAstroInstruments()` je `refresh()` (60 s), exklusiv für den Telemetry-Lock-Standort |
| — SEEING | Gauge | Skala 0–5″, Nadel amber | `spot.seeing` |
| — SHEAR (Dual) | Gauge, **zwei Nadeln** | dick/kurz = Bodenwind 0–40 km/h (amber >20, rot >30), fein/lang = Jetstream 0–60 m/s (amber >35, rot >45) | `spot.wind_gusts ∥ spot.jetstream` |
| — TAU DP | Gauge | 0–15 K, cyan | `spot.dewpoint_spread` |
| — DEW | Gauge, **inverse Skala** | 10K (sicher, +90°) → 0K (Frost, −90°), amber <3K, rot <1,5K (blinkt) | `spot.dewpoint_spread` |
| — ZENITH SQM | Gauge + Rollzählwerk-Box | Nadel 15–22 mag/arcsec²; Box zeigt `B{klasse}`, Mond-Radius wächst mit Illum | `spot.bortle_class` → `SQM_FROM_BORTLE`-Map (Frontend) |
| **Telemetry-Link** `[LINK: ORT]` | Top-Bar, links der Gauges, grün leuchtend | Monospace-Feld, `margin-left:auto` | `telemetryTarget` (localStorage `astro_telemetry`), gesetzt per Flightstick/Marker-Klick |
| **LED-Matrix** (Glareshield-Warnlampen) | Top-Bar neben SYSTEM SECURE | 4 quadratische LEDs (11px): VRN Sync (grün), Bias (amber), OBS Mode, UAP | `updateAstroInstruments()` |
| **HOTAS-Flightstick** | `#cockpit`-Kind, Bildschirmmitte unten, 0px-Sitz auf T-Track (Desktop); Mobile: `position:fixed`, `scale(0.6)` | SVG (Bezel/Schrauben wie Gauges): Faltenbalg-Basis, ergonomischer Griff (Chrom-Gradient, Hartlicht-Kanten), roter Feuerknopf; `pointerdown/move/up` + Pointer-Capture; Griff-Gruppe neigt um Pivot (50,80): `translate+rotate` | Peil-Vektor (0°=Nord) → `_bearingDeg()` gegen Kartenmitte → nächster Standort = neues Telemetry-Ziel (live) |
| **Lunar Horizon** (Attitude Indicator) | Linkes Panel | `buildLunar()`: identischer Bezel/Stil, ClipPath, Himmel (#0e2236) / Erde (#191008), weiße Horizontlinie (2.2px), Pitch-Striche; Horizont verschiebt sich `translate(0, alt/90·24)`; Mond fix auf der Achse (Flugzeug-Symbol-Analogie), Glow skaliert mit Illum | `updateLunarHorizon()` (best GO/MAYBE-Spot): `moon.max_alt`, `moon.illum` |
| **OBS-Kippschalter** (ASTRO OBSERVATION) | Master-Switch-Dock, rechts unten | Mechanischer BAT-Switch: Metallplatte + Sechskantmutter + verchromter Hebel; SAFE = Hebel unten (178°), ARMED = oben (2°) mit Snap-Animation; Drop-Shadows je Stellung; Labels leuchten zustandsabhängig | Checkbox `#obs-mode-sw` → GET/POST `/api/observation-mode` |
| **Comms-Terminal** | Rechtes Panel | Matrix-Terminal (Scanlines, grün), max 40 Zeilen, `.comms-alert` rot blinkend | `commsLog()` aus allen Ereignissen (Rating-Wechsel, Transit, DYNAMIC ABORT, OBS MODE …) |
| **Bias-Widget** | Rechtes Panel, Tab BIAS/BOT CMDS | Karten je Parameter + SVG-**Liniendiagramm** (Pfade starten mit M!, X-Achse echte Tagesdistanz, 5 Datumsticks, Y-Labels, 7-Tage solide + Gesamt gestrichelt) | Init: `/api/bias-stats` + `/api/bias-history?days=365` |
| **Karte** | `#map-wrap` (Grid-Mitte) | Leaflet, OSM-Tiles mit CSS-Invert-Filter (dunkel), Spot-Dots (Rating-Farben, Regenglow), LP-Layer, Radar-Tiles, Regen-Icon-Raster + Zeit-Scrubber, Warnpolygon | `/api/spots` 60 s, `/api/rain-grid` debounced (600 ms) bei moveend/zoom |
| **Target-Brackets** | Auf dem Marker des LINK-Ziels | CSS `::before/::after` an `.leaflet-marker-icon.target-locked .spot-marker` (3px rote C-Klammern, pulsierend) — Selektor muss die *innere* `.spot-marker`-Div treffen, da das divIcon-`className` leer ist | `updateTargetLock()` bei `setTelemetry()` + nach jedem `renderSpots()` |
| **Detail-Panel (Standort)** | Bottom-Sheet über Karte (z 1200) | Forecast-Stunden, Mond/Planeten, Transit Pills (`steps[]`: Walk/Bus/RNV farbig), DYNAMIC-ABORT-Banner | `/api/forecast?id=`, `/api/deployment?id=` on demand |

### 1.2 Layout-Container (IST)
- `#cockpit`: CSS-Grid, Areas `"top top top" / "left map right" / "bottom bottom bottom"`, Rows `auto 1fr 52px`, Columns `minmax(168px, max-content) 1fr 340px`; `position:fixed; inset:0`.
- Desktop-Collapse: Panel-Pins togglen `.collapse-left/.collapse-right` (Spalten auf 0).
- **Mobile (980px): vertikales Stacking** `"top"/"map"/"left"/"right"/"bottom"` — funktioniert, erzwingt aber **Vertikalscrollen** (Scrollhöhe ≈ 2300px) → bricht die Cockpit-Immersion. **Genau das ist der Migrationsauslöser.**
- Top-Bar Mobile: horizontale Wischleiste (overflow-x auto, 96px-Gauges, Scrollbar versteckt). Lesson 20: Mobile-Overrides brauchen höhere Spezifität als spätere Basis-Regeln (Doppel-ID).

### 1.3 Zielkonzept Mobile: Fullscreen-Map + Overlay-HUDs (struktural)

**Grundprinzip:** Karte füllt den gesamten Viewport (100dvh), ALLE Panels werden ein-/ausblendbare, halbtransparente Overlays. Kein Dokument-Scrollen mehr — nur noch Map-Pan und Overlay-Toggles.

**Z-Index-Strategie (Ziel-Bild):**

| Ebene | z-index | Inhalt | Verhalten |
|---|---|---|---|
| 0 Basis | (Leaflet-Panes ≤ 700) | Karte + Marker + Brackets + Einsatzweg-Linie | einziger scrollbarer Kontext (Map-Pan) |
| 1 HUD-Chrome | 900–999 | Flightstick (fixed, skaliert), Regen-Scrubber, Zoom-Buttons | permanent, pointer-events nur auf Controls |
| 2 Slide-Overlays | 1100 | **Links:** Sensoren + LUNAR HORIZON; **Rechts:** Bias + Comms | Slide-in von links/rechts (transform: translateX(±100%)), Backdrop transparent-klick-durch (nur bei offenem Overlay abfangend), max-width 78vw, eigene Scrollfläche im Overlay-Inneren |
| 3 Sheets | 1200 | Detail-Panel (Standort/Transit) als Bottom-Sheet | bestehendes Pattern unverändert übernehmbar |
| 4 System | 1300–1400 | Toast/Config-Overlay, evtl. Modal | — |

**Ableitungen für die Umsetzung (struktural, noch kein Code):**
- Grid ersetzen durch: `#map-wrap { position:fixed; inset:0 }` + Overlays als `position:fixed`-Kinder von `#cockpit`; `#cockpit` verliert `overflow-y:auto` (Mobile-Query invertieren: statt Stacking → Overlays).
- Toggle-Mechanik: die vorhandenen Panel-Pin-Buttons (`collapse-left/right`) werden zu Overlay-Toggles; Zustand in localStorage (analog `astro_telemetry`).
- `map.invalidateSize()` ist bereits an resize gebunden — nach Overlay-Schließen triggern.
- Flightstick bleibt fixed (Lesson: in Scroll-Containern scrollen absolut positionierte Kinder mit — fixed löst das).
- Gauges/Top-Bar bleiben obere Wischleiste (bewährt) ODER werden selbst zum Drop-Down-HUD — Empfehlung: Wischleiste behalten (kein Zusatzaufwand).

---

## 2. Sensorik & Daten-Pipelines (Frontend ➔ Backend)

### 2.1 Datenquellen (Crawler, systemd-getaktet)

| Timer | Takt | Job | Schreibt |
|---|---|---|---|
| `astro-radar.timer` | alle **5 Min** (`OnCalendar=*:2/5`, kalenderbasiert+persistent) | Radar-Tick: BrightSky-Live-Werte (Wind, Taupunkt, Radar-Status), Regen-2h, DWD-Warnungen | Latest-Werte je Standort in SQLite |
| `astro-crawler.timer` | alle **30 Min** (`OnCalendar=*:0/30`) | Heavy-Tick: ClearOutside → Open-Meteo → BrightSky (Fallback-Kette), Seeing/Jetstream (Open-Meteo-Modelle), Forecast-Serien 48 h, Bias-Recompute (kumulativ + 7-Tage-Rolling), Golden Windows, Rating (PROFILE_RULES DSO/Planet) | forecast.json, bias.json, forecast_verification (165k+ Zeilen), bias_history |
| Tages-Cache | 1×/Tag | skyfield/de421: Mond- & Planeten-Ephemeriden, Dunkelheitsfenster | Tages-Cache (lokal berechnet, keine API) |

### 2.2 Backend-Endpunkte (FastAPI, `astro-app/backend/main.py`)

| Endpunkt | Methode | Zweck | Frontend-Nutzung |
|---|---|---|---|
| `/api/spots` | GET | Kombinierter Spot-State (Heavy+Radar+Mond), `bortle_class`, `profile`, Ratings | Haupt-Loop, 60 s (`REFRESH_MS`), localStorage-Cache `astro_last_spots` für Offline |
| `/api/forecast` | GET (id/name, NFC-normalisiert) | Bias-korrigierte Stundenserie, verstrichene Stunden getrimmt, `incomplete`-Flag | Detail-Panel |
| `/api/deployment` | GET (id, home_lat/lon, setup_minutes) | Transit-Plan HQ→Standort (s. Sektion 3) | TRANSIT-ROUTTE-Button |
| `/api/rain-grid` | GET (bbox, zoom) | Open-Meteo-Multi-Koordinaten-Raster, 7 h seriell | Regen-Icons, debounced 600 ms bei moveend/zoomend + Scrubber |
| `/api/bias-stats`, `/api/bias-history` | GET | Bias-Transparenz (kumulativ/7d/Tag) | Bias-Tab, Init |
| `/api/observable` | GET | Limiting Magnitude (equipment.json) + Messier-Filter (skyfield alt/az) | Beobachtungsobjekte |
| `/api/observation-mode` | GET/POST | Master-Switch (gated proaktive Telegram-Pushes) | OBS-Kippschalter |
| `/api/telegram-commands` | GET | 12 echte Bot-Kommandos (TELEGRAM_COMMANDS) | BOT-CMDS-Tab |
| `/api/warnings` | GET | DWD-Warnpolygone | Warn-Layer |
| `/api/lp-tiles/{z}/{x}/{y}` | GET | Light-Pollution-Tiles (lokal, proxiert) | LP-Layer |
| `/api/watch` | POST/DELETE | Live-Spot (GPS, 2 h) | btn-gps |
| `/api/moon`, `/api/history`, `/api/bortle`, `/api/changelog` | GET | Nebendaten | Panels/Info |
| `/api/fwhm_sync` | POST | FWHM-Messungen (token-gated) | extern (Session-Nachtrag) |

### 2.3 Datenfluss im Frontend (Gauges)
`/api/spots` → `refresh()` (60 s) → `updateAstroInstruments(data)`: wählt `telemetrySpot()` (LOCK-Ziel, Fallback Spot[0]) → bedient alle 5 Gauges + LED-Matrix + Link-Label. `updateLunarHorizon()` separat (best GO/MAYBE). Ein Instrument crasht nicht den Loop (per-instrument try/catch-Praxis im Render-Pfad).

### 2.4 Offline-Strategie (Browser, IST)
- Service Worker **v44**, network-first, Shell + API-Cache, alte Caches beim Activate geräumt.
- `astro_last_spots` als Offline-Fallback („vor X Min"-Kennzeichnung), Info-Widget-Tabellen localStorage-gecacht, `astro_base` (Server-URL, Capacitor-relevant!), `astro_night`, `astro_telemetry`.

---

## 3. Transit & Routing-Engine (GTFS)

### 3.1 Datenbasis
- **VRN-GTFS-Static**, 153 MB, `~/gtfs`, Lizenz dl-de/by-2.0 (docs/SOURCE_LEGAL_REVIEW.md); 1,4 Mio stop_times, ~70k Trips, bbox-gefiltert.
- `GTFSStaticSource` (`app/sources/transit.py`): Kalenderfilter (calendar + calendar_dates) pro Service-Tag; **Tages-Cache** im Backend (`_GTFS_CACHE`), Kaltlauf ~8 s, warm ~0,1–1,1 s.

### 3.2 Router (`_search`, rundenbasiert, max. 2 Umstiege)
- **Alle Zeitvergleiche als GTFS-Offsets zum SERVICE-Tag** (`night`-Datum des Golden Windows), nicht zum Abfragetag — Fahrten nach Mitternacht sind 24:xx/25:xx kodiert (Lesson 19: abgeleitete Basen nur einmal berechnen).
- Runde 1: Direktfahrten ab Start (Boarding ab `min_dep` = Deadline −12 h, geklemmt auf Tagesbeginn).
- Runde 2: 1 Umstieg — Boarding nur an **Hub-Stops** (≥2 Routen) oder per **Fußweg-Umstieg ≤ 600 m** (`_walk_neighbors`, Zellgitter-Index ~550 m) — reale Fälle wie 625-Terminus „Feudenheim Bstg 2" → RNV 50 an „Bstg 1" (20 m).
- Runde 3: 2. Umstieg ab den in Runde 2 neu verbesserten Stops (RAPTOR-Pruning + `seen`-Dedup).
- **Pareto-Optionen pro Stop** (früheste Ankunft, späteste Erstabfahrt, Pfad): nötig für „letzte Bahn"-Semantik — sonst kombiniert der Router Morgen-Anfahrten mit Abend-Anschlüssen (Monster-Verbindungen >3 h werden gefiltert).
- Sortierung: Hinweg späteste Abfahrt zuerst, Rückweg früheste zuerst; Dedup über (dep, arr, lines, ziel).

### 3.3 Deployment-Logik (`app/engine/deployment.py`)
- Startpunkt fix: **HQ Ilvesheim** (49.4783726, 8.5662896) — Backend-Default `HQ_ILVESHEIM` + `home_lat/home_lon`-Parameter; kein Nutzer-GPS.
- `latest_departure`: letzte Verbindung, die vor `window_start − setup_buffer` ankommt; läuft das Fenster bereits, **Deadline = Fensterende** (Ankunft im laufenden Fenster erlaubt, kein leeres Ergebnis).
- `extraction`: früheste Rückverbindung nach Fensterende; `extraction_warning_ts` = 30 min vorher („Abbau").
- **Dynamic Abort:** Bei laufendem Fenster prüft `/api/deployment` die bias-korrigierte Stundenserie gegen `PROFILE_RULES` (`_hour_score`): kippt eine kommende Stunde auf NO-GO (Wolken/Seeing/Regen), wird `abort_at` übergeben — Rückfahrt-Suche startet am Umschlagpunkt. Frontend: rot blinkendes „DYNAMIC ABORT: WETTERUMSCHLAG - NÄCHSTE RÜCKFAHRT …" + Comms-Alert.
- Visuell: Einsatzweg als Doppel-Linie (schwarze Kontur 7px + rot gestrichelt `10,15`), HQ-Pin, `fitBounds`; **Transit Pills** aus `steps[]` (Walk 🚶/Bus blau/RNV-Tram amber, je mit Zeiten + Haltestellen-Tooltips).

---

## 4. App-Readiness (Native Plugins, Capacitor)

### 4.1 Genutzte Browser-APIs (IST) → natives Ziel

| Browser-API (heute) | Ort | Zweck | Capacitor-Ziel |
|---|---|---|---|
| `navigator.geolocation.getCurrentPosition` | `gpsWatch()` (btn-gps) | Live-Spot 2 h, HTTPS-Pflicht im Browser | **@capacitor/geolocation**; darüber hinaus **Background Geolocation** (`@capacitor-community/background-geolocation`) als Ersatz für den VPN-basierten Standort-Ansatz: Akku-schonend per Motion-Trigger statt Dauer-VPN aufs Heimnetz |
| `localStorage` | BASE-URL, `astro_last_spots`, `astro_night`, `astro_telemetry`, Info-Widget-Caches | Persistenz kleinere Zustände | bleibt funktionsfähig (Capacitor-WebView); für robuste Daten ggf. später Preferences/SQLite |
| Service Worker (v44, network-first) | `/sw.js` | Offline-Shell + API-Cache | in WebView aktiv; **Achtung:** `BASE`-URL-Konfiguration (`astro_base`) wird zum Muss, da die App nicht mehr same-origin zum Server läuft — `api()` läuft bereits konsequent über `BASE` ✓ |
| `navigator.serviceWorker`-Registrierung | app.js | — | unverändert |
| `alert()`/`confirm()` | GPS/Config-Feedback | simple Dialoge | später @capacitor/dialog (kosmetisch) |
| Pointer Events (Flightstick, Touch-Scrubber) | `#flightstick`, Scrubber | Interaktion | funktionieren in WebView unverändert |
| `matchMedia` | Collapse-Logik beim Load | Mobile-Erkennung | bleibt; ergänzt um `@capacitor/status-bar` (Fullscreen) |

### 4.2 Migrationshinweise (aus dem Audit abgeleitet)
1. **Layout zuerst (Sektion 1.3):** Overlay-Konzept umsetzen, DANACH Capacitor-Shell — sonst wrappt die App das Scroll-Layout nur ein.
2. **BASE-URL/HTTPS:** Tailscale-Serve-Endpoint fest in `astro_base`; Zertifikate im native-Context prüfen; API-TOKEN-Header-Mechanismus existiert (x-api-token) bereits.
3. **Background Geolocation statt VPN:** Der jetzige Ansatz „Live-Standort via VPN ins Heimnetz + getCurrentPosition beim Öffnen" wird obsolet — natives Plugin liefert Standorte auch bei geschlossener App (motion-triggered, akkuarm), der Server kann `POST /api/watch` direkt vom Gerät erhalten. Datenschutz: Koordinaten nur Richtung eigener Server (privat), nie ins Repo (locations.json ist gitignored).
4. **Push:** Telegram-Bot bleibt primärer Alert-Kanal; optional später @capacitor/push-notifications als Spiegel.
5. **Assets/Icons:** PWA-Icons existieren; native Icon-Sets ergänzen.
6. **Test-Gates unverändert:** ruff/pytest/Changelog-Deploy-Pipeline läuft serverseitig weiter — die App konsumiert nur den HTTPS-Endpoint.

---

*Erstellt als Read-Only-Audit; keine Code-Datei wurde verändert (nur dieses Dokument hinzugefügt).*
