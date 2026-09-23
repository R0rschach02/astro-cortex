# Native Shell (Phase 2)

- `setup_native.sh` einmalig auf einer Node-Maschine in DIESEM Verzeichnis ausfuehren
  (Capacitor 6, appId de.astrocortex.app, webDir ../frontend).
- AndroidManifest: NUR ACCESS_COARSE/FINE_LOCATION, GPS-Feature `required="false"`
  (Netzwerk-Fallback). Bewusst KEIN ACCESS_BACKGROUND_LOCATION, kein Wake-Lock,
  kein Foreground-Service - On-Demand-Ping-Philosophie.
- App.js nutzt `window.Capacitor.Plugins.Geolocation` (vom nativen Runtime
  injiziert) - kein Bundler, kein npm-Import im Web-Code.
- Erster Start: im Zahnrad-Menü die Server-URL (Tailscale-Serve HTTPS) setzen.
- Server-Auth: optional ASTRO_API_TOKEN in ~/.env + Token im App-Config-Menü.
