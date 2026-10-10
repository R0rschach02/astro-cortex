# IGEL_ARCHITECTURE.md — Astro Command Center Master-Dokument
**Version:** v1.1.8 (Build 10102108) · **OTA-Hash:** `28d2067-10102329`
**Datum:** 10.10.2026 · **Git:** `origin/main @ 65dd883` · **Server:** `api.teamigel.com`
**APK:** `https://api.teamigel.com/native/astro-cortex-v1.1.8.apk` (7.505.501 Bytes)

---

## System-Übersicht

| Komponente | Technologie | Pfad |
|---|---|---|
| Backend | Python 3.10, FastAPI, uvicorn | `~/astro-app/backend/main.py` |
| Frontend | Vanilla JS (kein Bundler), Leaflet.js | `~/astro-app/frontend/` |
| Native App | Capacitor 6 (Android), versionCode 10102108 | `~/astro-app/native/` |
| Datenbank | SQLite (`.astro_crawler.db`) | `~/.astro_crawler.db` |
| Crawler | Playwright + httpx, systemd Timer | `~/.zcode/workspace/default/astro_crawler.py` |
| Transit | VRN-GTFS-Static (153MB lokal), RAPTOR-Router | `~/app/sources/transit.py` |
| Ephemeriden | skyfield 1.55 + de421.bsp + NAIF-Kernel | `~/.skyfield/` |
| Tunnel | Cloudflare → `127.0.0.1:8000` | `api.teamigel.com` |
| Auth | Cloudflare Access (M2M Service Tokens) | `CF_ACCESS` in app.js |

---

## 1. Notification-Profile (Dual-Profile, Backend-Logik)

**Endpoint:** `GET /api/notifications/upcoming?hours_ahead=48`

### 🌙 LUNAR WINDOW

| Kriterium | Schwellwert | Warum |
|---|---|---|
| Mond-Höhe | **> 15°** | Mond muss sichtbar sein |
| Wolken | **< 30%** | Mond toleriert dünne Schleier |
| Seeing | **< 2,0″** | Krater-Detail braucht ruhige Luft |
| Bortle | **ignoriert** | Mond überstrahlt Lichtverschmutzung |

**Notification-Text:** `🌙 LUNAR WINDOW — {Ort} — Alt: 35°, Illum: 78%`

### 🌌 DEEP SKY WINDOW

| Kriterium | Schwellwert | Warum |
|---|---|---|
| Mond | **< 0° ODER < 15% illum** | Mond darf nicht stören |
| Wolken | **< 20%** | DSO braucht Dunkelheit |
| Bortle | **≤ 6** | Lichtverschmutzung ist der Killer |
| Seeing | **zweitrangig** | DSO toleriert > 2″ |

**Notification-Text:** `🌌 DEEP SKY WINDOW — {Ort} — Moon: Below horizon`

**JSON-Response-Format:**
```json
{
  "events": [
    {"type": "LUNAR_WINDOW", "location": "...", "moon_alt": 35, "moon_illum": 78, ...},
    {"type": "DEEP_SKY_WINDOW", "location": "...", "moon_status": "Below horizon", "bortle": 4, ...}
  ],
  "profiles": {"LUNAR_WINDOW": "...", "DEEP_SKY_WINDOW": "..."}
}
```

**Plugin:** `@capacitor/local-notifications` — Schedule-Format: Properties DIREKT (nicht unter `at:` verschachtelt!).

---

## 2. Alle Features

### 2.1 Cloud-Hunter (Lücke 3: Dynamischer Umkreis-Scan)

| Aspekt | Wert |
|---|---|
| Endpoint | `GET /api/cloud-hunter?radius_km=50&hours_ahead=3&max_bortle=6` |
| Grid | 10km-Raster, Zentrum Mannheim (49.48/8.63), bis 100 Punkte |
| Batch-Call | Ein Open-Meteo-Request mit komma-getrennten Koordinaten |
| Bortle-Filter | Statische Maske: 5 Lichtquellen (Mannheim, Heidelberg, Weinheim, Viernheim, Worms) |
| Scoring | `clouds_avg + bortle × 3 + wind_avg × 0.5` |
| UI | HUNTER-Button in Sensorleiste → Heatmap + ⭐ OPTIMUM-Marker |
| Comms | `[CLOUD-HUNTER] 61 Punkte, Optimum: 13% bei 42.6km` |

### 2.2 Inversions-Detektor (Lücke 1: Höhen-Differenzierung)

**9 Standorte** mit `elevation_m` in `~/locations.json` (gitignored).

| Bedingung | Schwellwert |
|---|---|
| Wolken | > 70% |
| Taupunkt-Spread | < 3,0 K |
| Wind | < 10 km/h |
| Höhe | > 200m |

**Korrektur:** `(Höhe − 100m) / 1000 × 100pp`, max 60pp.
- **Katzenbuckel** (626m): 100% → **47%**
- **Koenigsstuhl** (550m): 100% → **55%**

**UI:** Pulsierendes amber Badge `⛰ ÜBER INVERSION — 47% statt 100%`

### 2.3 Departure-Optimizer (Lücke 4: Time-to-Target)

**Endpoint:** `GET /api/departure-optimizer?setup_minutes=45`

**Logik-Kette:** `Fenster-Start − 45min Rüstzeit − Transitzeit = Latest Departure`

Transit-Schätzung: `dist_km / 40 km/h × 60min` (ÖPNV-Mischgeschwindigkeit, HQ=Ilvesheim).

| Status | Bedeutung |
|---|---|
| `GO` | >15min bis Abfahrt (grün) |
| `DEPARTURE_IMMINENT` | <15min (amber blinkend) |
| `MISSED` | verpasst (rot) |

### 2.4 Wolken-Bewegungsvektor (Lücke 2)

**Endpoint:** `GET /api/weather/movement`

Vergleicht Gesamtbewölkung jetzt vs. +2h (Open-Meteo), nutzt Windrichtung als Proxy.

| Trend | Bedeutung |
|---|---|
| `clearing` (grün) | Wolken nehmen ab → Standort-ETAs |
| `clouding` (rot) | Wolken nehmen zu |
| `stable` (grau) | Keine Änderung |

**UI (Top-Bar):** `☀ Aufklarend ↗ 25 km/h · Klart auf: Weinheim (~2h)`

### 2.5 Datenwirt-Erinnerung

- **Trigger:** `scheduleDailyReminders()` beim Kaltstart
- **Zeit:** 19:30 Uhr
- **Dedup:** `localStorage["astro_reminder_datenwirt_YYYY-MM-DD"]`
- **Inhalt:** `🔥 DATENWIRT-ERINNERUNG: Bitte Wolkenlage bewerten`

### 2.6 Ground Truth Panel

4 Buttons im Standort-Panel:
- 🌌 KLAR (5%) · ⛅ LÜCKEN (40%) · ☁️ DICHT (95%) · 🌫️ NEBEL (100% + Inversions-Markierung)

**Endpoint:** `POST /api/telemetry/ground_truth` → matcht sofort gegen Prognose → Delta im Comms

### 2.7 Bias-Korrektur (mit Deckel)

- Clouds + Seeing, ≤24h/>24h Buckets
- **Deckel:** max ±10pp Wolken, ±0.30″ Seeing
- Anzeige-only, Rating unkorrigiert
- Grund: Zirkuläre Verifikation (Prognose und "Realität" beide aus DWD)

---

## 3. OTA-Pipeline (v1.1.8 — Funktionierender Bypass)

### Architektur-Entscheidung: Capgo-Plugin umgangen

Der @capgo/capacitor-updater Plugin's interner HTTP-Client ist unzuverlässig (crashte bei 206 Partial Content, Version-Strings mit Bindestrichen, und Content-Length-Konflikten). Wir umgehen ihn komplett:

```
Kaltstart / UPLINK-Button
    ↓
checkOtaUpdate(force?)
    ↓ [24h-Drossel — force=true via UPLINK umgeht]
    ↓
fetch(BASE + "/updates/latest.json?t=" + Date.now())   ← Cache-Buster
    ↓ [400/404 → "Manifest nicht erreichbar" → Abbruch]
    ↓ [version=null → "Manifest ungueltig" → Abbruch]
    ↓
fetch(BASE + m.url)                                     ← Voller GET (kein Range!)
    ↓ [Über CapacitorHttp = native HTTP, BEWÄHRTE Pipeline]
    ↓ → zipBlob (162 KB)
    ↓
FileReader.readAsDataURL(zipBlob)
    ↓ → Base64 (Praefix mit indexOf(",") validiert und abgeschnitten)
    ↓
Filesystem.writeFile({path: "ota_VERSION.b64", directory: "DATA"})
    ↓ [FLAT path, KEINE Subdirectories, KEIN recursive]
    ↓
localStorage.setItem("astro_bundle_version", m.version)
    ↓
setTimeout(() => location.reload(), 2000)
```

### Server-Headers (Backend)

**`/updates/bundle.zip`:**
```
HTTP/2 200 OK
Content-Type: application/zip
Content-Length: 166235 (exakter Integer)
Accept-Ranges: none (keine 206-Antworten!)
Cache-Control: no-store, no-cache, must-revalidate, max-age=0
Pragma: no-cache
```

**WICHTIG:** `Accept-Ranges: none` ist gesetzt, aber `Range`-Requests IMMER NOCH mit 206 beantwortet werden können (von uvicorn). Der Pre-Flight sendet KEINEN Range-Header.

### Cloudflare-Konfiguration

| Regel | Pfad | Aktion |
|---|---|---|
| Cache Rule #1 | `/native/` | Bypass cache |
| Cache Rule #2 | `/updates/` | Bypass cache |
| Access Policy | `api.teamigel.com` | Service Auth (M2M) |

**CF-Access-Service-Tokens (in app.js als `CF_ACCESS`):**
```
CF-Access-Client-Id: 2cf0ebe34375ac9e58cd1aef99cb31d6.access
CF-Access-Client-Secret: cfast_u1pe4fQKo8pOoQKgv0lhmy1gQvruihuAfUr8Vat65f85f9c0
```
**Nicht in OTA-Downloads senden** (Bypass aktiv, Token-Header stören)!

### Pre-Flight-Check (im Frontend)

```
OTA-Precheck: HTTP 200 | CT: application/zip | CL: 166235
OTA-Precheck: Bundle geladen (162 KB)
```

Bei HTTP != 200: Download wird übersprungen, klare Fehlermeldung.

### Nuke-Cache (Notfall)

**3 Sekunden auf die Top-Bar drücken:**
1. Alle `localStorage`-Keys löschen
2. `CapacitorUpdater.deleteAll()` falls verfügbar
3. `location.reload()`

### UPLINK-Button

`uplinkNow()` ruft `checkOtaUpdate(true)` — **umgeht 24h-Drossel**.

### APK-Build-Prozess

```bash
cd ~/astro-app/native
export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"
npx cap sync android    # MUSS vor jedem Build laufen!
# versionCode/versionName in android/app/build.gradle setzen
bash build_apk.sh
cp android/app/build/outputs/apk/debug/app-debug.apk ~/astro-cortex-v{VERSION}.apk
```

### OTA vs. APK: Wann was

| Szenario | Lösung |
|---|---|
| Frontend-Änderung | `astro_deploy.sh` → OTA-Bundle → App zieht beim Start |
| Native/Plugin-Änderung | `build_apk.sh` → neue APK → manueller Download |

---

## 4. Deploy-Pipeline

```bash
~/astro_deploy.sh "<conventional message>"
```

Schritte: Syntax-Check → ruff (F8xx/E722/BLE001) → pytest → cp workspace→live → md5 → restart → curl-Beweis → OTA-Bundle (ZIP + Manifest + Hash) → git commit → push

**Crawler-Änderungen:** IMMER in `~/.zcode/workspace/default/astro_crawler.py` (Deploy synct workspace→live).

---

## 5. Standing Practices

1. **Proof-Level:** Playwright-Interaktionsbeweise (nicht nur DOM), Pixel-Diffs für visuelle Claims, VLM für Screenshots
2. **JS-Syntax-Check VOR Deploy:** `new Function(src)` in Playwright
3. **Deploy-Ausgabe NIE filtern** (Changelog-Gate versteckt sich sonst)
4. **LESSONS.md** führen (22 Einträge)
5. **APP_CAPABILITIES.md / IGEL_ARCHITECTURE.md** bei jedem Feature-Deploy aktualisieren
6. **Koordinaten nur in gitignorierten Dateien** (locations.json, equipment.json)
7. **Kollaborateur anonymisiert** in öffentlichen Dateien

---

## 6. Offene Todos für nächste Session

| # | Task | Priorität |
|---|---|---|
| 1 | `GET /updates/version` auf v1.1.8 aktualisieren (steht noch auf v1.1.4) | Hoch |
| 2 | APK-Dateiname in Backend auf `astro-cortex-v1.1.8.apk` setzen | Hoch |
| 3 | Primen-Window-Push im Crawler (`check_prime_window_push()`) an Dual-Profile anpassen (Telegram-Alerts) | Mittel |
| 4 | Signierter Release-Build statt Debug-APK | Mittel |
| 5 | Cloudflare Cache-Status der alten APK-URL prüfen (sollte nach TTL abgelaufen sein) | Niedrig |
| 6 | VRN-GTFS-Feed-Refresh (manuell) | Niedrig |
| 7 | Telegram-Token-Rotation (BotFather) | Niedrig |
| 8 | Responsive-UI-Epic umsetzen (`docs/EPIC_RESPONSIVE_UI.md`) | Mittel |

---

## 7. Environment (Server "seriousjoke")

```bash
# Node/npm für native Builds
export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"

# Java für Gradle
export JAVA_HOME=$HOME/jdk21

# Android SDK
export ANDROID_HOME=$HOME/android-sdk

# Python
source ~/ai_env/bin/activate

# Deploy
~/astro_deploy.sh "message"

# APK neu bauen
cd ~/astro-app/native && bash build_apk.sh

# Services
systemctl --user status astro-app astro-radar astro-crawler
journalctl --user -u astro-app -f
```

---

## 8. Verifikations-URLs

| URL | Zweck |
|---|---|
| `https://api.teamigel.com/updates/version` | Server-Version prüfen |
| `https://api.teamigel.com/updates/latest.json` | OTA-Manifest |
| `https://api.teamigel.com/updates/bundle.zip` | OTA-Bundle |
| `https://api.teamigel.com/native/astro-cortex-v1.1.8.apk` | APK-Download |
| `https://api.teamigel.com/api/spots` | Hauptdaten |
| `https://api.teamigel.com/api/notifications/upcoming` | Dual-Profile Events |
| `https://api.teamigel.com/api/cloud-hunter` | Umkreis-Scan |
| `https://api.teamigel.com/api/weather/movement` | Bewegungsvektor |
| `https://api.teamigel.com/api/departure-optimizer` | Time-to-Target |

*Dieses Dokument ist der vollständige Save-State für die nächste Session. Übergebe es als erstes Briefing.*
