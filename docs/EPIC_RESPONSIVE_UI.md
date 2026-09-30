# EPIC: Fully Responsive Adaptive UI (alle Gerätklassen)

**Status:** Offen · **Typ:** Frontend-Epic · **Zielgeräte:** Desktop-Monitore
(≥1600px), Laptops (~1280px), Tablets (~768–1024px), Smartphones (~390–430px)
— Endgeräte des Betreibers und des Kollaborateurs.

## Ziel
Das Cockpit skaliert dynamisch und ohne manuelle Zoom-Gesten auf allen
Geräteklassen. Keine fixen Desktop-px-Größen mehr als alleiniger Maßstab —
elementebezogene Breakpoint-Strategie statt Einzellösung.

## User Stories
1. **Tablet (768–1024px)**: Zwischen Handy-Overlay- und Desktop-Grid-Modell
   gibt es aktuell eine Bruchkante — das UI soll auf Tablets das
   Desktop-Grid mit proportional verkleinerten Instrumenten nutzen
   (Gauges via `clamp()`, Panels via `minmax()`), Overlays erst <768px.
2. **Laptop (1280px)**: Gauges/Panels dürfen die Karte nicht verdrängen —
   Messgröße: Map-Breite ≥ 45 % des Viewports bei geöffneten Panels.
3. **Smartphone**: Bestehendes Overlay-Modell (Vollbild-Karte + Slide-HUDs)
   beibehalten; zusätzlich Schriftgrößen/DPI-Kalibrierung prüfen
   (`-webkit-text-size-adjust`, kleine Schriftgrößen <9px eliminieren).
4. **Alle**: Comms-Terminal, Transit-Pills und Bias-Chart müssen ohne
   horizontales Clipping funktionieren (Teilumbruch für Pills ist ok).

## Akzeptanzkriterien
- Playwright-Verifikung bei 390 / 768 / 1280 / 1600 px: kein horizontaler
  Dokument-Scroll, keine Überlappung von HUD-Chrome (Flightstick/T-Track/
  Topbar), Bedienbarkeit von UPLINK/DATALINK/Overlays auf jeder Breite.
- VLM-Screenshot-Abnahme je Gerät klasse.
- Desktop-Verhalten (Grid + Collapse-Pins) bleibt regression sfrei.

## Technische Anknüpfungspunkte
- `astro-app/frontend/style.css` (Media-Queries 980px, clamp-Werte)
- Lesson 20: Mobile-Overrides brauchen höhere Spezifität als spätere
  Basis-Regeln (ID-Selektoren).
- OTA-Auslieferung — keine APK-Rebuilds nötig.
