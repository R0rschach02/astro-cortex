# Astro Command Center (astro-cortex)

24/7-Wettercrawler + Go/No-Go-Entscheidungssystem für Amateur-Astronomie:
Kampfjet-Cockpit-PWA (Vanilla JS, kein Bundler), FastAPI-Backend, VRN-GTFS-
ÖPNV-Routing ab Equipment-HQ, Telegram-Bot mit Golden-Window- und
Dynamic-Abort-Warnungen, native Android-App (Capacitor) mit OTA-Live-Updates.

## Zugriff auf das System (Zugriffsmodell)

| Gerätetyp | Zugang | Auth |
|---|---|---|
| **Desktop-PCs** (feste Workstations) | `https://seriousjoke.tailcc473e.ts.net` (Tailscale Serve) | Tailscale-Mitgliedschaft |
| **Laptops, Tablets & Smartphones** | **zwingend über die native App** (Android/Capacitor) bzw. `https://api.teamigel.com` (Cloudflare Tunnel) | Cloudflare Access: M2M-Service-Token (App, automatisch) |

- Der Cloudflare-Tunnel (`api.teamigel.com`) leitet auf `127.0.0.1:8000` und
  ist durch Cloudflare Access geschützt. Die App authentifiziert sich
  maschinell per Service-Token-Headern (CapacitorHttp, kein CORS/OTP/Cookie).
- Die Pfade `/updates/*` (OTA-Bundles) und `/native/*` (APK-Download) sind
  per Cloudflare-Bypass öffentlich erreichbar (sonst könnte der Updater das
  Bundle nicht laden, da er keine Custom-Header mitsendet).
- Deployment von Frontend-Änderungen als OTA-Bundle: `~/astro_deploy.sh`
  erzeugt automatisch `~/updates/bundle.zip` + Manifest; die App zieht sie
  beim (gedrosselten, max. 1×/24 h) Kaltstart-Check. APK-Rebuild
  (`~/astro-app/native/build_apk.sh`) nur noch bei nativen/Plugin-Änderungen.

## Komponenten

- `astro_crawler.py` — Crawler + Rating + Bias + Bot (systemd-Timer:
  Radar 5 min / Heavy 30 min, jeweils `TZ=Europe/Berlin` erzwungen)
- `astro-app/backend/main.py` — FastAPI (127.0.0.1:8000) + PWA + APK-Route
- `astro-app/frontend/` — Cockpit-PWA (Gauges, HOTAS-Flightstick, HUD-Overlays)
- `astro-app/native/` — Capacitor-Projekt (Android, OTA via @capgo/updater)
- `app/` — GTFS-Transit-Engine, Deployment-Logik, Libration/Kolongitude
- `tests/` — 107 deterministische Tests (Deploy-Gate)
- `SYSTEM_AUDIT.md`, `LESSONS.md`, `docs/` — Architektur & Fallstudien

## Hinweise

- Koordinaten/Equipment liegen in gitignorierten Dateien (`locations.json`,
  `equipment.json`); der Kollaborateur ist im öffentlichen Repo anonymisiert.
- Deploy ausschließlich über `~/astro_deploy.sh` (Syntax → ruff → pytest →
  OTA-Bundle → Commit → Push).
