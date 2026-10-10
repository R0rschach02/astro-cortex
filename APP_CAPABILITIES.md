# APP_CAPABILITIES.md — Astro Command Center v1.1.0
**Stand:** 10.10.2026 · **APK:** v1.1.0 (Build 10102100) · **OTA-Manifest:** `/updates/latest.json`

---

## 1. Datenwirt-Erinnerung

| Aspekt | Implementierung |
|---|---|
| **Trigger** | `scheduleDailyReminders()` beim App-Kaltstart (nativ) |
| **Zeitpunkt** | 19:30 Uhr Ortszeit |
| **Dedup** | `localStorage["astro_reminder_datenwirt_YYYY-MM-DD"]` — max. 1×/Tag |
| **Inhalt** | 🔥 DATENWIRT-ERINNERUNG: „Bitte Wolkenlage bewerten — Blick nach oben!" |
| **Plugin** | `@capacitor/local-notifications` |
| **Comms-Log** | `ERINNERUNG: Datenwirt 19:30 Uhr geplant` |

## 2. OTA-Diagnose

| Schritt | Comms-Meldung | Fehlerbehandlung |
|---|---|---|
| **Check-Start** | `OTA: Pruefe... (installiert: v1.1.0)` | Zeigt Version vor dem Vergleich |
| **Manifest-Fetch** | — | Fehlschlag → `OTA: Manifest nicht erreichbar (HTTP xxx)` + sauberer Abbruch |
| **Version identisch** | `OTA: aktuell (2de9ad8-...)` | — |
| **Download** | `OTA: Lade Bundle 2de9ad8-...` | Fehlschlag → `OTA-Download fehlgeschlagen: <Grund> | Installiert: <Version>` |
| **Erfolg** | `OTA AKTIV: alt → neu — App startet neu` | Version wird in localStorage persistiert |
| **Drossel** | `OTA: gedrosselt (max 1x/24h, letzter Check Xh her)` | UPLINK-Button umgeht mit `force=true` |

**Download:** Ohne Token-Header (CF-Bypass für `/updates` aktiv). Endpoints: `/updates/latest.json` + `/updates/manifest.json` (Alias) + `/updates/bundle.zip`.

## 3. Standort Katzenbuckel & Inversions-Detektor

### Katzenbuckel (Odenwald)
- **ID:** `katzenbuckel` · **Koordinaten:** 49.4706 / 9.0401
- **Höhe:** 626m · **Bortle:** 4
- **Notiz:** Höchster Berg im Odenwald, Top-Ausweichspot für Inversionswetterlagen

### Inversions-Detektor (`_inversion_adjusted()`)

| Bedingung | Schwellwert | Bedeutung |
|---|---|---|
| Wolken total | > 70% | DWD meldet bedeckt |
| Taupunkt-Spread | < 3,0 K | Feuchte Luft am Boden (Inversionsgrenze niedrig) |
| Wind | < 10 km/h | Keine Durchmischung |
| Höhe | > 200m | Standort über Nebelgrenze |

**Korrektur-Formel:** `(Höhe − 100m) / 1000 × 100pp`, max 60pp.

| Standort | Höhe | Korrektur bei Inversion |
|---|---|---|
| Katzenbuckel | 626m | **−52,6pp** (100% → 47%) |
| Koenigsstuhl | 550m | **−45,0pp** (100% → 55%) |
| Weinheim | 150m | — (unter 200m-Schwelle) |

**Comms-Log:** `[INVERSION DETECTED] Katzenbuckel (626m) Wolken 100% -> 47%`

**UI:** Pulsierendes amber Badge im Standort-Panel: `⛰ ÜBER INVERSION — 47% statt 100%`

## 4. Wolken-Bewegungsvektor

| Aspekt | Implementierung |
|---|---|
| **Endpoint** | `GET /api/weather/movement` |
| **Datenquelle** | Open-Meteo: `cloud_cover` + `wind_direction_10m` + `wind_speed_10m` |
| **Vergleich** | Gesamtbewölkung jetzt vs. +2h |
| **Trend** | `delta < −15pp` = Aufklarend · `> +15pp` = Eintrübend · sonst Stabil |
| **Bewegungsrichtung** | `wind_direction + 180°` (Wind kommt aus X → Wolken bewegen sich nach X+180°) |
| **Standort-Vorhersage** | Wenn Trend=clearing: Standorte in Bewegungsbahn (<60° Winkelabweichung) mit ETA |
| **Update-Takt** | 60s (refresh-Zyklus) |
| **Comms-Log** | `[MOVEMENT] Aufklarend | 55deg | 25.2 km/h` |

**UI (Top-Bar):** `☀ Aufklarend ↗ 25 km/h · Klart auf: Weinheim (~2h)`

## 5. Cloud-Hunter (Dynamischer Umkreis-Scan)

| Aspekt | Implementierung |
|---|---|
| **Endpoint** | `GET /api/cloud-hunter?radius_km=50&hours_ahead=3&max_bortle=6` |
| **Grid** | 10km-Raster, Zentrum Mannheim (49.48/8.63), bis zu 100 Punkte |
| **Batch-Call** | Ein einzelner Open-Meteo-Request mit komma-getrennten Koordinaten |
| **Bortle-Filter** | Statische Maske: 5 Lichtquellen (Mannheim, Heidelberg, Weinheim, Viernheim, Worms) mit Radius + Bortle-Schätzung |
| **Scoring** | `clouds_avg + bortle × 3 + wind_avg × 0.5` (niedrig = gut) |
| **UI** | HUNTER-Button in Sensorleiste → Heatmap (grün/amber/orange/rot) + ⭐ OPTIMUM-Marker |
| **Comms-Log** | `[CLOUD-HUNTER] 61 Punkte, Optimum: 13% bei 42.6km` |

## 6. Departure-Optimizer (Time to Target)

| Aspekt | Implementierung |
|---|---|
| **Endpoint** | `GET /api/departure-optimizer?setup_minutes=45` |
| **Logik-Kette** | `Fenster-Start → −45min Rüstzeit → −Transitzeit = Latest Departure` |
| **Transit-Schätzung** | Distanzbasiert: `dist_km / 40 km/h × 60min` (ÖPNV-Mischgeschwindigkeit) |
| **HQ** | Ilvesheim (49.4784 / 8.5663) |
| **Status** | `GO` (>15min bis Abfahrt) · `DEPARTURE_IMMINENT` (<15min, blinkt) · `MISSED` (verpasst) |
| **UI** | Countdown-Zeilen im linken Panel unter BEST SITE |
| **Comms-Log** | `[DEPARTURE] GO: Weinheim Abfahrt 04:56 (19min Fahrt)` |
| **Update-Takt** | 60s (refresh-Zyklus) |

## 7. Ground Truth (Datenwirt-Datensammlung)

| Aspekt | Implementierung |
|---|---|
| **Endpoint** | `POST /api/telemetry/ground_truth` |
| **Buttons** | 🌌 KLAR (5%) · ⛅ LÜCKEN (40%) · ☁️ DICHT (95%) · 🌫️ NEBEL (100% + Inversions-Markierung) |
| **Matching** | Server matcht sofort gegen letzte Prognose → `delta = forecast − actual` |
| **Datenbank** | `ground_truth_log` Tabelle in `.astro_crawler.db` |
| **Comms-Log** | `GT #5: KLAR 5% → Delta: +85 pp ⚠ Modell deutlich daneben!` |

## 8. PRIME-WINDOW-Benachrichtigungen

| Aspekt | Implementierung |
|---|---|
| **Endpoint** | `GET /api/notifications/upcoming?hours_ahead=48` |
| **Kriterium** | Wolken ≤75% des Durchschnitts ODER Seeing ≤80% des Durchschnitts |
| **Plugin** | `@capacitor/local-notifications` (sofortige Benachrichtigung) |
| **Dedup** | `localStorage["astro_notified_windows"]` (max 50 Einträge) |
| **Comms-Log** | `NOTIF: PRIME WINDOW Katzenbuckel 2026-10-11 18:00 geplant` |

## 9. Übersicht aller Endpoints

| Route | Methode | Feature |
|---|---|---|
| `/api/spots` | GET | Hauptdaten (9 Standorte) |
| `/api/forecast` | GET | Bias-korrigierte Stundenserie |
| `/api/deployment` | GET | VRN-GTFS Transit-Routing |
| `/api/cloud-hunter` | GET | **L3:** Umkreis-Scan |
| `/api/weather/movement` | GET | **L2:** Bewegungsvektor |
| `/api/departure-optimizer` | GET | **L4:** Time-to-Target |
| `/api/notifications/upcoming` | GET | PRIME-WINDOW-Push |
| `/api/telemetry/ground_truth` | GET/POST | **Datenwirt** |
| `/updates/latest.json` | GET | OTA-Manifest |
| `/updates/manifest.json` | GET | OTA-Manifest (Alias) |
| `/updates/bundle.zip` | GET | OTA-Bundle |
| `/native/astro-cortex.apk` | GET | APK-Download |
