# IGEL_ARCHITECTURE.md — Astro Command Center Master-Dokument
**Version:** v1.1.9 (Build 10102109) · **OTA-Hash:** `siehe /updates/latest.json`
**Datum:** 11.10.2026 · **Git:** `origin/main` · **Server:** `api.teamigel.com`
**APK:** `https://api.teamigel.com/native/astro-cortex-v1.1.9.apk`

**v1.1.9 Sicherheits-/Architektur-Release:** (1) CF-Secret-Leak behoben — Tokens kommen vom Server-Endpoint statt aus dem Code; (2) OTA installiert jetzt WIRKLICH (CU.download→CU.set→notifyAppReady, kein Mock-Green); (3) Batterieschutz — appStateChange stoppt alle Timer im Hintergrund.

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

## 3. OTA-Pipeline (v1.1.9 — Echte Installation über das Capgo-Plugin)

### Architektur-Entscheidung: Plugin-Download rehabilitiert (206 serverseitig gekillt)

Der v1.1.8-"Bypass" (fetch → .b64 → toter Ballast, "OTA AKTIV" ohne Wirkung = Mock-Green) ist ENTFERNT. Zwei Erkenntnisse aus dem Plugin-Source (v8.52.1): (a) Es gibt KEINE API, um fertige Bytes zu übergeben — download({url,version}) + set({version}) ist der einzige Installationsweg. (b) Frische Downloads senden keinen Range-Header; nur Resume-Pfade tun es. Der 206-Crash ist seit v1.1.9 serverseitig unmöglich: Die Middleware entfernt den Range-Header VOR dem Routing → FileResponse liefert IMMER 200 komplett + exakte Content-Length.

```
Kaltstart / UPLINK-Button
    ↓
checkOtaUpdate(force?)
    ↓ [24h-Drossel — force=true via UPLINK umgeht]
    ↓
fetch(BASE + "/updates/latest.json?t=" + Date.now())   ← KEINE Token-Header (Bypass-Pfad)
    ↓ [version=null/url=null → "Manifest ungueltig" → Abbruch]
    ↓ [version == localStorage → "OTA: aktuell" → Ende]
HEAD-Precheck (Status + Content-Type + Content-Length, ohne Body)
    ↓ [!=200 → Abbruch]
CU.download({url, version: safeVersion, checksum: sha256?})
    ↓ [Plugin lädt via OkHttp NATIV, entpackt ZIP ins Bundle-Verzeichnis]
    ↓ [Fehler → ehrliche Fehlermeldung, Version bleibt UNVERÄNDERT]
localStorage.setItem("astro_bundle_version", m.version)   ← erst NACH erfolgreichem Download
CU.set({version: safeVersion})
    ↓ [WebView-Pointer wird physisch auf das neue Bundle gesetzt, App-Neustart]
setTimeout(location.reload, 4000)                          ← Sicherheitsnetz
```

**notifyAppReady()**: Muss in jedem Bundle (auch APK-Code) laufen — sonst revertiert Capgo nach appReadyTimeout (10 s) still auf das vorherige Bundle. Seit v1.1.9 im Startup.

**Checksumme**: astro_deploy.sh schreibt jetzt die VOLLE SHA-64 in latest.json; CU.download() verifiziert sie optional mit.

### Server-Headers (Backend)

**`/updates/bundle.zip`:**
```
HTTP 200 OK (auch auf Range-Requests — Header wird in der Middleware gestrippt)
Content-Type: application/zip
Content-Length: exakter Integer
Accept-Ranges: none
Cache-Control: no-store, no-cache, must-revalidate, max-age=0
Pragma: no-cache
```

### Token-Bootstrap (v1.1.9)

`GET /updates/app-bootstrap.json` liefert die M2M-Credentials aus `~/.astro_secrets/cf_access.json` (liegt bewusst im Access-Bypass wie bundle.zip; bewusste Abwägung: schützt vor Mitlaeufern, nicht vor Zielgerichteten — daführ zentrale Rotation ohne APK). Die App holt sie bei jedem Start und cachet sie im localStorage (`astro_cf_id`/`astro_cf_secret`).

### Cloudflare-Konfiguration

| Regel | Pfad | Aktion |
|---|---|---|
| Cache Rule #1 | `/native/` | Bypass cache |
| Cache Rule #2 | `/updates/` | Bypass cache |
| Access Policy | `api.teamigel.com` | Service Auth (M2M) |

**CF-Access-Service-Tokens — NICHT mehr im Code/Repo (Leak 10/2026, rotiert):**
```
Server-Seite:  ~/.astro_secrets/cf_access.json   (gitignored, chmod 600)
App-Seite:     GET /updates/app-bootstrap.json   (CF-Bypass-Pfad, kein Token nötig)
Rotation:      Datei am Server austauschen → App zieht beim nächsten Start
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
| ~~1~~ | ~~`GET /updates/version` aktualisieren~~ — **erledigt v1.1.9** (dynamisch + APK-Download-Name) | ✅ |
| ~~2~~ | ~~APK-Dateiname in Backend setzen~~ — **erledigt v1.1.9** (Default 1.1.9) | ✅ |
| 3 | Prime-Window-Push im Crawler (`check_prime_window_push()`) an Dual-Profile anpassen (Telegram-Alerts) | Mittel |
| 4 | Signierter Release-Build statt Debug-APK | Mittel |
| 5 | OTA-Feldtest auf dem Gerät: Kaltstart nach v1.1.9-APK → Comms muss "OTA AKTIV" + echten Neustart zeigen (nicht nur Log) | Hoch |
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
