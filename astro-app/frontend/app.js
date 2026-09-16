/* Astro Command Center - Frontend-Logik (vanilla JS, keine Build-Tools).
 *
 * Backend-Adresse konfigurierbar (Capacitor-sicher): BASE_URL liegt im
 * localStorage, leer = gleiche Origin (Default beim via Tailscale Serve
 * ausgelieferten Betrieb). Alle Fetches laufen ausschliesslich darueber.
 * Offline: Service Worker cached die Shell; der letzte /api/spots-Stand
 * landet zusaetzlich im localStorage und wird mit "vor X Min" angezeigt.
 */
"use strict";

const BASE = (localStorage.getItem("astro_base") || "").replace(/\/$/, "");
const $ = (id) => document.getElementById(id);
const REFRESH_MS = 60_000;

let map, markersLayer, warnLayer, lpLayer, rainGridLayer;
let rgActive = false, rgDebounce = null, stormRings = [];
let rgHour = 0, rgLastData = null;   // Zeitregler 0-6 h fuer die Regen-Icons
let rgPlaying = false, rgTimer = null;
let lastSpots = null;
let CURRENT_PROFILE = "dso";
let currentSpot = null;   // fuer Tab-Wechsel im Detail-Panel
let currentTab = "now";

/* ---------- Hilfen ---------- */
function fmt(v, unit) { return (v === null || v === undefined) ? "n/a" : v + (unit || ""); }
function ratingColor(rating) {
  return rating === "GO" ? "#35d07f" : rating === "MAYBE" ? "#f5c542"
       : rating === "NO-GO" ? "#ff5252" : "#93a1b3";
}
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

async function api(path, opts) {
  const res = await fetch(BASE + path, opts);
  if (!res.ok) throw new Error(path + " -> HTTP " + res.status);
  return res.json();
}

/* ---------- Karte ---------- */
function initMap() {
  map = L.map("map", { zoomControl: true, tap: true })
        .setView([49.54, 8.63], 10);

  // Basiskarten: direkt OSM (CARTO verlangt seit 2026 einen API-Key -
  // siehe LESSONS.md Fall 8). Dezent entsaettigt via CSS (className),
  // Rotlicht-Dimming greift ueber die bestehende body.night-Kachelregel.
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18, subdomains: "abc", className: "basemap-tile",
    attribution: '&copy; OpenStreetMap-Mitwirkende',
  }).addTo(map);

  // Lichtverschmutzung (Lorenz-Atlas via Backend-Proxy, Disk-Cache dort).
  // maxNativeZoom 6: darueber fragt Leaflet die 6er-Kacheln ab und skaliert.
  lpLayer = L.tileLayer(BASE + "/api/lp-tiles/{z}/{x}/{y}", {
    maxNativeZoom: 6, maxZoom: 18, opacity: 0.55,
    attribution: "Light Pollution: D. Lorenz (VIIRS)",
  });

  // DWD-Warnpolygone (GeoJSON vom Backend)
  warnLayer = L.geoJSON(null, {
    style: (f) => {
      const k = f.properties.kind;
      return {
        color: k === "storm" ? "#ff2a00" : k === "rain" ? "#3fa9f5" : "#9aa5b1",
        weight: 2, fillOpacity: k === "storm" ? 0.35 : 0.18,
        dashArray: k === "other" ? "4 6" : null,
      };
    },
    onEachFeature: (f, layer) => layer.bindPopup(
      `<b>${esc(f.properties.event)}</b><br>${esc(f.properties.description || "")}`),
  });

  markersLayer = L.layerGroup().addTo(map);

  // Regionales Regen-Icon-Raster (Open-Meteo via /api/rain-grid): die neue
  // Standard-Regenansicht - klare Wolke/Tropfen-Icons statt Farbflaechen.
  rainGridLayer = L.layerGroup().addTo(map);
  rgActive = true;

  // Vorhersage-Zuverlaessigkeit + Bot-Befehle: einklappbares Widget
  // top-left (data-cached, offline-faehig via localStorage).
  initInfoWidget();

  // Zeitregler: spult die Icons durch die OM-Stundenprognose (0 = jetzt).
  // Play/Pause laeuft automatisch durch - self-rescheduling setTimeout wie
  // beim RainViewer-Loop (robuster als setInterval), kein neuer Request.
  const rgPanel = document.createElement("div");
  rgPanel.id = "rg-time";
  rgPanel.innerHTML = `<button id="rg-play" title="Regen-Verlauf 0-6 h automatisch abspielen">\u25b6</button>
    <input id="rg-slider" type="range" min="0" max="6"
      step="1" value="0" aria-label="Regen-Prognose Stunden">
    <span id="rg-label">Jetzt</span>`;
  document.body.appendChild(rgPanel);
  $("rg-slider").addEventListener("input", (e) => {
    setRgHour(Number(e.target.value));
  });
  $("rg-play").addEventListener("click", rgTogglePlay);

  // RainViewer: Dummy-Overlays nur fuer die Control, Logik via Events.
  // Kachel-Heatmap ist seit dem Icon-Raster nur noch optionale Rohansicht.
  const rvRawDummy = L.layerGroup();
  const rvSatDummy = L.layerGroup();
  L.control.layers(null, {
    "Lichtverschmutzung": lpLayer,
    "Unwetterwarnungen (DWD)": warnLayer,
    "Regen-Icons (Region)": rainGridLayer,
    "Radar-Rohansicht (Kachel)": rvRawDummy,
    "Wolken (Satellit)": rvSatDummy,
  }, { position: "bottomright", collapsed: true }).addTo(map);
  map.on("overlayadd", (e) => {
    if (e.name.includes("Regen-Icons")) {
      rgActive = true;
      $("rg-time")?.classList.remove("hidden");
      fetchRainGrid();
    }
    if (e.name.includes("Radar-Rohansicht")) rvStart("radar");
    if (e.name.includes("Satellit")) rvStart("satellite");
  });
  map.on("overlayremove", (e) => {
    if (e.name.includes("Regen-Icons")) {
      rgActive = false;
      $("rg-time")?.classList.add("hidden");
    }
    if (e.name.includes("Radar-Rohansicht") || e.name.includes("Satellit")) rvStop();
  });
  // Raster folgt dem Ausschnitt (debounced); Cache im Backend faengt Pan an
  map.on("moveend zoomend", () => {
    if (!rgActive) return;
    clearTimeout(rgDebounce);
    rgDebounce = setTimeout(fetchRainGrid, 600);
  });
}

/* ---------- Ampel-Schwellen (bestätigt 2026-08-15) ----------
   grün/gelb/rot je Parameter; Rating-Schwellen (Wolken 20/40, Seeing 2.0)
   stammen 1:1 aus der Crawler-Logik. Rückgabe: 'g' | 'y' | 'r' | null(n/a). */
const TH = {
  seeing:    (v) => v == null ? null : v <= 1.0 ? "g" : v <= 2.0 ? "y" : "r",
  jet:       (v) => v == null ? null : v <= 15  ? "g" : v <= 30  ? "y" : "r",
  clouds:    (v) => v == null ? null : v <= 20  ? "g" : v <= 40  ? "y" : "r",
  rain:      (v) => v == null ? null : v <= 10  ? "g" : v <= 30  ? "y" : "r",
  precip:    (v) => v == null ? null : v <= 0.1 ? "g" : v <= 1.0 ? "y" : "r",
  wind:      (v) => v == null ? null : v <= 15  ? "g" : v <= 30  ? "y" : "r",
  gusts:     (v) => v == null ? null : v <= 25  ? "g" : v <= 40  ? "y" : "r",
  tau:       (v) => v == null ? null : v >= 6   ? "g" : v >= 3   ? "y" : "r",
  temp:      (v) => v == null ? null : v <= 18  ? "g" : v <= 25  ? "y" : "r",  // 600D ungekuehlt
  rh:        (v) => v == null ? null : v <= 80  ? "g" : v <= 90  ? "y" : "r",
  moonIll:   (v) => v == null ? null : v <= 25  ? "g" : v <= 60  ? "y" : "r",
  moonAlt:   (v) => v == null ? null : v > 30   ? "g" : "r",
  planetAlt: (v) => v == null ? null : v > 30   ? "g" : "r",
};
function dot(cls) {
  return cls ? `<span class="dot d-${cls}"></span>` : `<span class="dot d-na"></span>`;
}
function row(k, v, cls) {
  return `<div class="k">${k}</div><div class="v ${cls || ""}">${v}</div>`;
}

/* ---------- Vorausschau-Tab ---------- */
function cell(v, fmtFn) {
  const cls = fmtFn(v);
  return `<td class="${cls ? "c-" + cls : "c-na"}">${v ?? "–"}</td>`;
}

function nightLabel(night) {
  // Absolute Datumsangabe statt relativem "Heute/+1/+2": das night-Feld
  // (YYYY-MM-DD) ist im Forecast-Datensatz vorhanden - reine Anzeige-Sache.
  const WD = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
  const y = +night.slice(0, 4), m = +night.slice(5, 7), d = +night.slice(8, 10);
  const dt = new Date(y, m - 1, d);
  if (isNaN(dt)) return esc(night);
  return `${WD[dt.getDay()]}, ${String(d).padStart(2, "0")}.${String(m).padStart(2, "0")}.${y}`;
}

function forecastHtml(fc) {
  const g = fc.golden;
  const others = (fc.golden_windows || []).filter(w => w !== g);
  const goldenCard = g
    ? `<div class="golden">
         <div class="g-title">✨ Golden Window ${g.night === new Date().toISOString().slice(0,10) ? "heute Nacht" : ""}</div>
         <div class="g-time">${esc(g.start)} – ${esc(g.end)} Uhr</div>
         <div class="g-why">${g.reasons.map(esc).join(" · ")}</div>
         ${others.length ? `<div class="g-why" style="margin-top:4px">Weitere: ${others.map(w =>
            `${w.night.slice(5)} ${esc(w.start)}-${esc(w.end)} (${w.hours}h)`).join(" · ")}</div>` : ""}
       </div>`
    : `<div class="golden none">
         <div class="g-title">Kein brauchbares Fenster in den nächsten Nächten</div>
         <div class="g-why">${esc(CURRENT_PROFILE === "planet"
              ? "Seeing/Jetstream/Wolken erfüllen nie gleichzeitig die Kriterien"
              : "Es fehlt vermutlich an Dunkelheit, Wolken oder Seeing")}</div>
       </div>`;
  // Stunden nach Nacht gruppieren (Segmente), innerhalb chronologisch
  const segs = [];
  for (const h of fc.series) {
    if (!segs.length || segs[segs.length-1].night !== h.night)
      segs.push({night: h.night, rows: []});
    segs[segs.length-1].rows.push(h);
  }
  const rows = segs.map(seg => `
    <tr class="night-sep"><td colspan="8">${nightLabel(seg.night)}</td></tr>` +
    seg.rows.map(h => `
    <tr class="${h.ok ? "row-ok" : ""}">
      <td class="c-h">${esc(h.hhmm)}</td>
      ${cell(h.clouds, TH.clouds)}
      ${h.beyond_seeing ? '<td class="c-na" title="Meteoblue-Horizont überschritten">–</td>'
                        : cell(h.seeing, TH.seeing)}
      ${cell(h.wind, TH.wind)}
      ${cell(h.tau, TH.tau)}
      ${cell(h.rain, TH.rain)}
      <td class="${h.dark ? "c-g" : "c-na"}">${h.dark ? "🌙" : "☀"}</td>
      <td class="${h.moon_up ? "c-y" : "c-na"}">${h.moon_up ? "🌕" : ""}</td>
    </tr>`).join("")).join("");
  return `
    <div class="sub mono">Prognose ${esc(fc.ts || "")} · Profil ${esc(fc.profile)} ·
      Seeing bis ${esc((fc.seeing_horizon || "").slice(11,16) || "Horizont")} ·
      Wolken: ${esc(fc.sources && fc.sources.clouds || "?")}/OM ·
      Dunkel heute ${esc(fc.dark_window || "n/a")}</div>
    ${goldenCard}
    <table class="fc-table mono">
      <thead><tr><th>Std</th><th>Wolk%</th><th>See"</th><th>Wind</th>
        <th>Tau</th><th>Reg%</th><th>🌌</th><th>🌕</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="sub" style="margin-top:8px">
      Mond: ${fc.moon_illum != null ? fc.moon_illum + "% " : "n/a "}
      ${esc(fc.moon_window || "")}
    </div>`;
}

function showTab(tab) {
  currentTab = tab;
  $("tab-now").classList.toggle("active", tab === "now");
  $("tab-fc").classList.toggle("active", tab === "fc");
  if (!currentSpot) return;
  if (tab === "now") {
    $("panel-content").innerHTML = panelHtml(currentSpot);
  } else {
    $("panel-content").innerHTML =
      '<div class="sub">lade Vorausschau…</div>';
    api(`/api/forecast?name=${encodeURIComponent(currentSpot.name)}`)
      .then(fc => {
        if (currentTab === "fc")
          $("panel-content").innerHTML = forecastHtml(fc);
      })
      .catch(e => {
        $("panel-content").innerHTML =
          `<div class="sub">Vorausschau nicht verfügbar: ${esc(e.message)}</div>`;
      });
  }
}

function lpLine(s) {
  const key = `astro_lp_${s.lat.toFixed(3)},${s.lon.toFixed(3)}`;
  let b = null;
  try { b = JSON.parse(localStorage.getItem(key)); } catch (e) { /* noch nicht geladen */ }
  if (!b) return row("Zenit-Lichtverschm.", "lade…");
  const cls = b.zone_index <= 4 ? "g" : b.zone_index <= 9 ? "y" : "r";
  const tip = CURRENT_PROFILE === "planet"
    ? "Planetarisch: Filter irrelevant"
    : b.zone_index >= 10 ? "Triband Pflicht · UHC schwach"
    : b.zone_index >= 8 ? "Antlia Triband empfohlen"
    : "Himmel dunkel genug · Triband optional";
  return row(dot(cls) + " Zenit-Lichtverschm.",
              `Zone ${esc(b.zone)} · ${b.mag} mag/arcsec² <span class='dim'>≈Bortle ${b.bortle}</span>`)
       + row("Filter-Tipp", tip);
}

function fetchBortle(s) {
  const key = `astro_lp_${s.lat.toFixed(3)},${s.lon.toFixed(3)}`;
  if (localStorage.getItem(key)) return;
  api(`/api/bortle?lat=${s.lat}&lon=${s.lon}`)
    .then(b => {
      localStorage.setItem(key, JSON.stringify(b));
      if (currentSpot === s && currentTab === "now") showTab("now");
    }).catch(() => {});
}

/* ---------- RainViewer: Regenradar + Satellit (animiert, direkt, kein Proxy) */
const RV_API = "https://api.rainviewer.com/public/weather-maps.json";
let rvState = { frames: [], layers: [], idx: 0, playing: false,
                timer: null, refetch: null, kind: null, active: false };

async function rvFetchFrames(kind) {  // kind: 'radar' | 'satellite'
  const d = await (await fetch(RV_API)).json();
  if (kind === "radar") {
    return { host: d.host, frames: [...(d.radar.past || []),
                                     ...(d.radar.nowcast || [])] };
  }
  return { host: d.host, frames: d.satellite?.infrared || [] };
}

function rvTimestampEl() {
  let el = document.getElementById("rv-ts");
  if (!el) {
    el = document.createElement("div");
    el.id = "rv-ts";
    el.innerHTML = '<button id="rv-play" class="hbtn rv-btn">&#9654;</button>' +
                   '<span id="rv-time" class="mono"></span>';
    document.getElementById("map").appendChild(el);
    document.getElementById("rv-play").onclick = rvTogglePlay;
  }
  return el;
}

async function rvStart(kind) {
  rvState.kind = kind; rvState.active = true;
  let data;
  try { data = await rvFetchFrames(kind); }
  catch (e) { rvBail("Radar-API nicht erreichbar"); return; }
  if (!data.frames.length) {
    rvBail(kind === "satellite" ? "Satellit: keine Bilder verfuegbar"
                                : "Radar: keine Frames");
    return;
  }
  const opts = kind === "radar" ? "/2/1_1" : "/0/0_0";  // color/smooth bzw. 0/0
  rvState.host = data.host;
  rvState.opts = opts;
  rvState.frames = data.frames;
  // LAZY: nur den NEUESTEN Frame sofort laden. Alle 13 Frames vorzuladen
  // feuert ~300 Kachel-Requests als Burst - RainViewer antwortet mit 429
  // (Too Many Requests) und gerade der sichtbare Frame bleibt leer.
  // Aeltere Frames entstehen erst bei ihrem ersten Loop-Auftritt und liegen
  // danach im Browser-Cache.
  rvState.layers = data.frames.map(() => null);
  rvState.idx = rvState.frames.length - 1;
  rvShow(rvState.idx);
  rvTimestampEl().style.display = "flex";
  rvTogglePlay(true);
  // Frames alle 5 min auffrischen, solange aktiv
  rvState.refetch = setInterval(async () => {
    if (!rvState.active) return;
    try {
      const nd = await rvFetchFrames(kind);
      if (nd.frames.length && nd.frames.length !== rvState.frames.length) {
        rvStop(false); rvStart(kind);
      }
    } catch (e) { /* naechster Versuch kommt */ }
  }, 300000);
}

function rvLayerFor(i) {
  if (!rvState.layers[i]) {
    const f = rvState.frames[i];
    rvState.layers[i] = L.tileLayer(
      `${rvState.host}${f.path}/256/{z}/{x}/{y}${rvState.opts}.png`,
      // RainViewer Free liefert ab z8 nur "Zoom Level Not Supported"-Kacheln;
      // z7 ist die hoechste Stufe mit echten Daten, darueber skaliert Leaflet hoch.
      { opacity: 0, className: "rv-tile", zIndex: 350, maxNativeZoom: 7, maxZoom: 18 }
    ).addTo(map);
  }
  return rvState.layers[i];
}

function rvShow(i) {
  rvState.idx = i;                      // Loop-Position mitfuehren
  rvState.layers.forEach((l, j) => {
    if (l) l.setOpacity(j === i ? rvOpacity() : 0);
  });
  rvLayerFor(i).setOpacity(rvOpacity());   // lazy: Layer ggf. erst jetzt erzeugen
  const f = rvState.frames[i];
  const t = new Date(f.time * 1000);
  rvTimestampEl().style.display = "flex";   // Existenz sicherstellen
  const span = document.getElementById("rv-time");
  span.textContent =
    (f.time * 1000 > Date.now() ? "Nowcast " : "") +
    t.toLocaleTimeString("de-DE", {hour: "2-digit", minute: "2-digit"});
}

function rvOpacity() {
  return document.body.classList.contains("night") ? 0.45 : 0.75;
}

function rvTogglePlay(force) {
  const want = force === true ? true : !rvState.playing;
  rvState.playing = want;
  document.getElementById("rv-play").innerHTML = want ? "&#10074;&#10074;" : "&#9654;";
  clearTimeout(rvState.timer);
  clearInterval(rvState.timer);
  if (want) {
    // Self-rescheduling Timeout statt setInterval: robust gegen
    // Timer-Throttling (Frame-Wechsel erst nach Rendering des vorigen)
    const step = () => {
      rvShow((rvState.idx + 1) % rvState.frames.length);
      rvState.timer = setTimeout(step, 700);
    };
    rvState.timer = setTimeout(step, 700);
  }
}

function rvStop(hideTs = true) {
  rvState.active = false; rvState.playing = false;
  clearTimeout(rvState.timer); clearInterval(rvState.timer); clearInterval(rvState.refetch);
  rvState.layers.forEach(l => map.removeLayer(l));
  rvState.layers = []; rvState.frames = [];
  if (hideTs) rvTimestampEl().style.display = "none";
}

function rvBail(text) {
  rvStop();
  alert(text);
  // Control-Checkbox zuruecksetzen
  document.querySelectorAll(".leaflet-control-layers-selector").forEach(cb => {
    if (cb.checked && (cb.closest("label").textContent.includes("Radar") ||
                       cb.closest("label").textContent.includes("Satellit")))
      cb.checked = false;
  });
}

/* ---------- Regen-Icon-Raster (Open-Meteo, Overworld-Bildsprache) ----------
   Keine Farbflaechen: pro Gitterpunkt eine Wolke mit 0-3 Tropfen,
   Groesse/Fuellung nach mm; hohle Wolke = Regen absehbar; Blitz-Symbol,
   wo der Punkt in einer aktiven DWD-Gewitterwarnung liegt. */

function setRgHour(h) {
  rgHour = h;
  $("rg-slider").value = h;
  $("rg-label").textContent = h === 0 ? "Jetzt" : `+${h} h`;
  if (rgLastData) renderRainGrid(rgLastData);
}

 function rgTogglePlay() { 
  rgPlaying = !rgPlaying; 
  const btn = $("rg-play");
   if (rgPlaying) { 
    btn.textContent = "\u23f8";
     btn.title = "Regen-Verlauf pausieren";
      rgAdvance();
     } else { 
      btn.textContent = "\u25b6"; 
      btn.title = "Regen-Verlauf 0-6 h automatisch abspielen"; 
      clearTimeout(rgTimer);
       rgTimer = null;
       } 
      } 
      
      
      function rgAdvance() { 
        if (!rgPlaying) 
          return; 
        const next = (rgHour + 1) % 7;
         rgHour = next; 
         setRgHour(next); 
         $("rg-slider").value = next; 
         rgTimer = setTimeout(rgAdvance, 1500); 
        } 


function rgIconHtml(p, storm) {
  const mm = p.mm ?? 0, prob = p.prob ?? 0;
  let cls = "", drops = 0;
  if (mm > 5)        { cls = "rg-4"; drops = 3; }
  else if (mm > 2)   { cls = "rg-3"; drops = 3; }
  else if (mm > 0.5) { cls = "rg-2"; drops = 2; }
  else if (mm > 0)   { cls = "rg-1"; drops = 1; }
  else if (prob >= 30) cls = "rg-forecast";
  if (!cls) return null;
  const when = rgHour === 0 ? "jetzt" : `in +${rgHour} h`;
  let html = `<div class="rg-icon ${cls}" title="${when}: ${mm.toFixed(1)} mm \u00b7 `
    + `${prob ?? "?"}% Regenwahrscheinlichkeit">`;
  html += `<span class="rg-cloud"></span>`;
  for (let i = 1; i <= drops; i++) html += `<span class="rg-drop rg-d${i}"></span>`;
  if (storm) html += `<span class="rg-bolt">\u26a1</span>`;
  return html + "</div>";
}

function pointInRing(lat, lon, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
    if ((yi > lat) !== (yj > lat) &&
        lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function renderRainGrid(data) {
  rainGridLayer.clearLayers();
  let shown = 0;
  for (const p of data.points) {
    // Zeitregler: Werte der gewaehlten Prognosestunde (Fallback: current)
    const src = (p.hours && p.hours[rgHour]) || p;
    const mm = src.mm ?? 0, prob = src.prob ?? 0;
    const storm = stormRings.some(r => pointInRing(p.lat, p.lon, r));
    const html = rgIconHtml({ ...p, mm, prob }, storm);
    if (!html) continue;
    shown++;
    L.marker([p.lat, p.lon], {
      icon: L.divIcon({ className: "", html, iconSize: [38, 34], iconAnchor: [19, 30] }),
      keyboard: false, zIndexOffset: -500,
    }).addTo(rainGridLayer);
  }
  console.debug(`[RegenIcons] ${shown}/${data.points.length} Gitterpunkte mit Icon (+${rgHour}h)`);
}

async function fetchRainGrid() {
  try {
    const b = map.getBounds();
    const bbox = [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()]
      .map(v => v.toFixed(4)).join(",");
    const data = await api(`/api/rain-grid?bbox=${encodeURIComponent(bbox)}&zoom=${map.getZoom()}`);
    rgLastData = data;
    renderRainGrid(data);
  } catch (e) { console.warn("rain-grid offline:", e); }
}

/* ---------- Marker + Detail-Panel ---------- */
// Tropfen-Stufen ankoppelt an die TH.precip-Ampelgrenzen (0.1 / 1.0 mm) + Starkstufe
const RAIN_STEPS = [
  { max: 0.1, cls: "rain-1", label: "Nieselregen" },
  { max: 1.0, cls: "rain-2", label: "leichter Regen" },
  { max: 2.5, cls: "rain-3", label: "kräftiger Regen" },
  { max: Infinity, cls: "rain-4", label: "Starkregen" },
];
let lastRainMm = {};   // name -> precip_2h des vorherigen Refresh (fuer Regen-Puls)

function rainBadgeHtml(spot, isNew) {
  const mm = spot.precip_2h, prob = spot.rain_prob || 0;
  if (mm == null || mm <= 0) {
    if (prob >= 50)
      return `<div class="rain-badge rain-forecast" title="Regen absehbar (${prob}% innerhalb 4 h)"></div>`;
    return "";
  }
  const st = RAIN_STEPS.find(x => mm <= x.max);
  return `<div class="rain-badge ${st.cls}${isNew ? " rain-new" : ""}" title="${st.label}: ${mm.toFixed(1)} mm/2 h"></div>`;
}

function markerIcon(spot, rainNew) {
  const rating = spot.rating || "NA";
  const alertCls = (spot.radar_status || "").includes("Alert") ? " alert" : "";
  return L.divIcon({
    className: "",
    html: `<div class="spot-marker">
             <div class="spot-dot rating-${esc(rating)}${alertCls}"></div>
             ${rainBadgeHtml(spot, rainNew)}
             <div class="spot-label">${esc(spot.name)}</div>
           </div>`,
    iconSize: [46, 46], iconAnchor: [23, 23],
  });
}

function panelHtml(s) {
  const m = s.moon || {};
  const isPlanet = CURRENT_PROFILE === "planet";
  const badge = `<span class="badge ${esc(s.rating || "NO DATA")}">${esc(s.rating || "NO DATA")}</span>`
    + (s.radar_status ? `<span class="badge">${esc(s.radar_status)}</span>` : "")
    + (s.is_live ? `<span class="badge">LIVE</span>` : "")
    + (s.dew_risk ? `<span class="badge dew-${esc(s.dew_risk)}">${
        s.dew_risk === "hoch" ? "⚠ Beschlag: Fangspiegel!" :
        s.dew_risk === "mittel" ? "Beschlag: mittel" : "Beschlag: gering"}</span>` : "");

  const heavyAge = s.age_min != null ? `Heavy vor ${s.age_min} Min` : "Heavy: n/a";
  const radarAge = s.radar_age_min != null ? `Radar vor ${s.radar_age_min} Min` : "Radar: n/a";
  const lmh = s.clouds_lmh || [null, null, null];
  const temp = (s.night_temp_min != null && s.night_temp_max != null)
    ? `${s.night_temp_min.toFixed(0)} – ${s.night_temp_max.toFixed(0)} °C` : "n/a";
  const planets = s.planets || {};
  const PLABEL = { jupiter: "Jupiter", saturn: "Saturn", mars: "Mars" };
  const planetRows = Object.keys(PLABEL)
    .filter(k => planets[k])
    .map(k => row(dot(TH.planetAlt(planets[k].max_alt)) + " " + PLABEL[k],
                  `${planets[k].culm} (${fmt(planets[k].max_alt, "°")})` +
                  (planets[k].window ? ` · >30° ${esc(planets[k].window)}` : " · nie >30°")))
    .join("");

  return `
    <h2>${esc(s.name)}</h2>
    <div class="sub mono">Stand: ${esc(s.ts || "unbekannt")} · Profil: ${isPlanet ? "PLANETARISCH" : "DSO"}</div>
    <div>${badge}</div>
    <div class="grp mono">${dot(TH.clouds(s.clouds_total))}${dot(TH.seeing(s.seeing))} <b>Wolken · Seeing · Jetstream</b><span class="age">${esc(heavyAge)}</span></div>
    <div class="kv mono">
      ${row(dot(TH.seeing(s.seeing)) + " Seeing", fmt(s.seeing, "&quot;") + ` (Idx ${s.seeing_index ?? "-"} /5)`)}
      ${row(dot(TH.jet(s.jetstream)) + " Jetstream", fmt(s.jetstream, " m/s"))}
      ${row(dot(TH.clouds(s.clouds_total)) + " Wolken total", fmt(s.clouds_total, " %"))}
      ${row(dot(TH.clouds(lmh[0])) + " Wolken L / M / H", `${fmt(lmh[0], "")} / ${fmt(lmh[1], "")} / ${fmt(lmh[2], "")} %`)}
    </div>
    <div class="grp mono">${dot(TH.precip(s.precip_2h))}${dot(TH.wind(s.wind_speed))} <b>Radar · Regen · Wind · Tau</b><span class="age">${esc(radarAge)}</span></div>
    <div class="kv mono">
      ${row(dot(TH.rain(s.rain_prob)) + " Regen (4 h)", fmt(s.rain_prob, " %"))}
      ${row(dot(TH.precip(s.precip_2h)) + " Niederschlag (2 h)", fmt(s.precip_2h, " mm"))}
      ${row(dot(TH.wind(s.wind_speed)) + " Wind (max 2 h)", fmt(s.wind_speed, " km/h"))}
      ${row(dot(TH.tau(s.dewpoint_spread)) + " Tau-Spread (min 2 h)", fmt(s.dewpoint_spread, " K"))}
    </div>
    <div class="grp mono">${dot(TH.temp(s.night_temp_max))}${dot(TH.gusts(s.wind_gusts))} <b>Nacht · Boden</b><span class="age">Nachtverlauf (30-Min)</span></div>
    <div class="kv mono">
      ${row(dot(TH.temp(s.night_temp_max)) + " Temp (Nacht min–max)", temp)}
      ${row(dot(TH.rh(s.night_rh_max)) + " Feuchte (max)", fmt(s.night_rh_max, " %"))}
      ${row(dot(TH.gusts(s.wind_gusts)) + " Böen (Nacht max)", fmt(s.wind_gusts, " km/h"))}
    </div>
    <div class="grp mono">${dot(TH.moonAlt(m.max_alt))} <b>Mond · Dunkelheit</b><span class="age">Tages-Stand (skyfield)</span></div>
    <div class="kv mono">
      ${row(dot(TH.moonIll(m.illum)) + " Mond-Illumination", m.illum !== undefined ? fmt(m.illum, " %") + " <span class='dim'>(DSO)</span>" : "n/a")}
      ${row(dot(TH.moonAlt(m.max_alt)) + " Mond-Kulmination", m.culm ? `${esc(m.culm)} (${fmt(m.max_alt, "°")})` : "n/a")}
      ${row("Mond &gt; 30°", m.window ? esc(m.window) : "nie in dieser Nacht")}
      ${row("Astron. Dunkelheit", s.dark_window ? esc(s.dark_window) : "n/a")}
      ${lpLine(s)}
    </div>
    <div class="grp mono"><b>Planeten &gt; 30°</b><span class="age">de421 · lokal</span></div>
    <div class="kv mono">${planetRows || row("Planeten", "keine Daten")}</div>
    <div class="sub" style="margin-top:10px">
      Wolkenquelle: ${esc(s.clouds_source || "n/a")} &middot; ${isPlanet
        ? "Planetarisch: Seeing/Jetstream hart, Mond & Beschlag irrelevant"
        : "DSO: Beschlag hart (keine Tauheizung), Mond-Ampel = DSO-Eignung"}
    </div>`;
}

function renderSpots(data) {
  markersLayer.clearLayers();
  for (const s of data.spots) {
    const mm = s.precip_2h || 0;
    const rainNew = mm > 0 && (lastRainMm[s.name] ?? 0) <= 0;   // trocken -> nass
    const mk = L.marker([s.lat, s.lon], { icon: markerIcon(s, rainNew) });
    mk.on("click", () => {
      currentSpot = s;
      $("panel").classList.remove("hidden");
      showTab("now");
      fetchBortle(s);
    });
    markersLayer.addLayer(mk);
    lastRainMm[s.name] = mm;
  }
}

/* ---------- Daten + Aktualitaet ---------- */
async function refresh() {
  try {
    const data = await api("/api/spots");
    lastSpots = data;
    localStorage.setItem("astro_last_spots", JSON.stringify(data));
    if (data.profile) {
      CURRENT_PROFILE = data.profile;
      updateModeButton();
    }
    renderSpots(data);
    // Warnungen nachladen (Layer nur, wenn aktiviert); Gewitter-Ringe
    // speichern wir zusaetzlich fuer die Blitz-Icons im Regen-Raster
    try {
      const warns = await api("/api/warnings");
      warnLayer.addData({ type: "FeatureCollection",
                          features: warns.features.filter(f => f.properties.kind !== "other") });
      stormRings = [];
      for (const f of warns.features) {
        if (f.properties.kind !== "storm" || !f.geometry) continue;
        if (f.geometry.type === "Polygon") stormRings.push(f.geometry.coordinates[0]);
        else if (f.geometry.type === "MultiPolygon")
          f.geometry.coordinates.forEach(p => stormRings.push(p[0]));
      }
    } catch (e) { console.warn("warnings offline:", e); }
    if (rgActive) fetchRainGrid();
    setFreshness(data.ts, false);
  } catch (e) {
    console.warn("refresh fehlgeschlagen:", e);
    const cached = localStorage.getItem("astro_last_spots");
    if (cached) { renderSpots(JSON.parse(cached)); setFreshness(JSON.parse(cached).ts, true); }
    else { setFreshness(null, true); }
  }
}

function setFreshness(ts, stale) {
  const el = $("freshness");
  if (!ts) { el.textContent = stale ? "offline - kein Cache" : "lade…"; return; }
  const age = Math.max(0, Math.round((Date.now() - new Date(ts).getTime()) / 60000));
  el.textContent = (stale ? "OFFLINE - " : "") + `vor ${age} Min`;
}

/* ---------- Aktionen ---------- */
function updateModeButton() {
  const b = $("btn-mode");
  b.textContent = CURRENT_PROFILE === "planet" ? "🪐" : "🌌";
  b.title = CURRENT_PROFILE === "planet"
    ? "Profil: PLANETARISCH (klicken für DSO)"
    : "Profil: DSO (klicken für Planetarisch)";
}

async function toggleMode() {
  const next = CURRENT_PROFILE === "planet" ? "dso" : "planet";
  $("btn-mode").textContent = "…";
  try {
    await api("/api/profile", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile: next }),
    });
    await refresh();
  } catch (e) {
    alert("Profilwechsel fehlgeschlagen: " + e.message);
    updateModeButton();
  }
}

function toggleNight() {
  document.body.classList.toggle("night");
  localStorage.setItem("astro_night", document.body.classList.contains("night") ? "1" : "0");
}

function gpsWatch() {
  if (!navigator.geolocation) { alert("Geolocation hier nicht verfügbar (HTTPS nötig)."); return; }
  $("btn-gps").textContent = "…";
  navigator.geolocation.getCurrentPosition(async (pos) => {
    try {
      const r = await api("/api/watch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat: pos.coords.latitude, lon: pos.coords.longitude, hours: 2 }),
      });
      map.setView([pos.coords.latitude, pos.coords.longitude], 12);
      alert(`Live-Standort aktiv für 2 h:\n${r.name}\nRadar: ${r.radar_status}`);
      refresh();
    } catch (e) { alert("Watch fehlgeschlagen: " + e.message); }
    $("btn-gps").textContent = "\u25CE";
  }, (err) => {
    alert("GPS-Fehler: " + err.message);
    $("btn-gps").textContent = "\u25CE";
  }, { enableHighAccuracy: true, timeout: 15000 });
}

function configure() {
  const cur = localStorage.getItem("astro_base") || "(gleiche Adresse wie diese Seite)";
  const v = prompt("Server-Adresse (leer = automatisch).\nSpäter z.B. die Tailscale-HTTPS-URL:", cur === "(gleiche Adresse wie diese Seite)" ? "" : cur);
  if (v === null) return;
  localStorage.setItem("astro_base", v.trim());
  location.reload();
}

/* ---------- Zahnrad-Menü + Changelog ---------- */
function toggleCfgMenu(force) {
  const m = $("cfg-menu");
  const show = force !== undefined ? force : m.classList.contains("hidden");
  m.classList.toggle("hidden", !show);
}

async function showChangelog() {
  toggleCfgMenu(false);
  const ov = $("changelog");
  ov.classList.remove("hidden");
  $("changelog-list").innerHTML = '<div class="sub">lade…</div>';
  try {
    const d = await api("/api/changelog");
    $("changelog-list").innerHTML = d.entries.map(e => `
      <article class="cl-entry">
        <div class="cl-head">
          <span class="cl-date">${esc(e.date)}</span>
          <span class="cl-title">${esc(e.title)}</span>
          ${e.tag ? `<span class="cl-tag">${esc(e.tag)}</span>` : ""}
        </div>
        <div class="cl-desc">${esc(e.desc || "")}</div>
        ${e.usage ? `<div class="cl-usage">&#9656; ${esc(e.usage)}</div>` : ""}
      </article>`).join("");
  } catch (err) {
    $("changelog-list").innerHTML =
      `<div class="sub">Changelog nicht verfügbar: ${esc(err.message)}</div>`;
  }
}

/* ---------- Start ---------- */
window.addEventListener("DOMContentLoaded", () => {
  if (localStorage.getItem("astro_night") === "1") document.body.classList.add("night");
  initMap();
  $("btn-refresh").onclick = refresh;
  $("btn-mode").onclick = toggleMode;
  $("btn-night").onclick = toggleNight;
  $("btn-gps").onclick = gpsWatch;
  $("btn-config").onclick = () => toggleCfgMenu();
  $("menu-config").onclick = () => { toggleCfgMenu(false); configure(); };
  $("menu-changelog").onclick = showChangelog;
  $("changelog-close").onclick = () => $("changelog").classList.add("hidden");
  document.addEventListener("click", (ev) => {
    const m = $("cfg-menu");
    if (!m.classList.contains("hidden")
        && !m.contains(ev.target) && ev.target.id !== "btn-config") {
      toggleCfgMenu(false);
    }
  });
  $("panel-close").onclick = () => $("panel").classList.add("hidden");
  $("tab-now").onclick = () => showTab("now");
  $("tab-fc").onclick = () => showTab("fc");
  refresh();
  setInterval(refresh, REFRESH_MS);
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(e => console.warn("SW:", e));
  }
});


/* ---------- Info-Widget: Vorhersage-Zuverlaessigkeit + Bot-Befehle ---------- */
const BIAS_LABELS = {
  clouds_le24h: ["Wolken \u226424 h", "pp", "kurzfristig"],
  clouds_gt24h: ["Wolken >24 h", "pp", "langfristig"],
  seeing_le24h: ["Seeing \u226424 h", "\u2033", "kurzfristig"],
  seeing_gt24h: ["Seeing >24 h", "\u2033", "langfristig"],
};
const BIAS_COLORS = {
  clouds_le24h: "#4db2ff", clouds_gt24h: "#0a4aa8",
  seeing_le24h: "#ffd24a", seeing_gt24h: "#ff8c42",
};

function biasTendenz(bucket, bias) {
  if (bias == null) return "zu wenig Daten \u2013 keine Korrektur aktiv";
  const [name, unit, wo] = BIAS_LABELS[bucket] || [bucket, ""];
  const b = Math.abs(bias);
  if (b < 0.3) return `${name}: sehr akkurat`;
  const richtung = bias < 0 ? "zu optimistisch" : "zu vorsichtig";
  const staerke = b > 10 ? "deutlich" : b > 3 ? "merklich" : "leicht";
  return `System ${richtung} (${wo}), ${staerke}: real ~${b.toFixed(1)} ${unit} ${bias < 0 ? "mehr" : "weniger"} als angesagt`;
}

function initInfoWidget() {
  const btn = document.createElement("button");
  btn.id = "info-widget-btn";
  btn.title = "Vorhersage-Zuverlaessigkeit & Bot-Befehle";
  btn.innerHTML = "\u{1F4CA}";
  const panel = document.createElement("div");
  panel.id = "info-widget";
  panel.className = "hidden";
  panel.innerHTML = `
    <div class="iw-head">
      <div class="iw-tabs">
        <button class="iw-tab active" data-tab="bias">Vorhersage-Zuverlaessigkeit</button>
        <button class="iw-tab" data-tab="bot">Telegram-Befehle</button>
      </div>
      <button id="iw-close" title="Schlie\u00dfen">\u2715</button>
    </div>
    <div id="iw-body"></div>
    <div class="iw-toggle-row">
      <div class="iw-toggle-caption">ASTRO&nbsp;OBSERVATION<br>
        <small>Wetter-Alarme nur aktiv, wenn gelegt</small></div>
      <label class="guard-switch" title="Beobachtungs-Modus schalten">
        <input type="checkbox" id="obs-mode-sw">
        <span class="guard-frame"><span class="guard-cover"></span>
          <span class="guard-on">ON</span><span class="guard-off">OFF</span></span>
      </label>
    </div>`;
  document.body.appendChild(btn);
  document.body.appendChild(panel);
  btn.addEventListener("click", () => {
    panel.classList.toggle("hidden");
    if (!panel.classList.contains("hidden")) loadInfoWidget();
  });
  panel.querySelector("#iw-close").addEventListener("click",
    () => panel.classList.add("hidden"));
  // Beobachtungs-Schalter: Zustand laden, Aenderung sofort POSTen
  const sw = panel.querySelector("#obs-mode-sw");
  api("/api/observation-mode").then(d => {
    sw.checked = !!d.observation_mode;
  }).catch(() => {});
  sw.addEventListener("change", async () => {
    sw.disabled = true;
    try {
      const d = await api("/api/observation-mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active: sw.checked }) });
      sw.checked = !!d.observation_mode;
    } catch (e) { console.warn("observation-mode:", e); sw.checked = !sw.checked; }
    sw.disabled = false;
  });
  panel.querySelectorAll(".iw-tab").forEach(t =>
    t.addEventListener("click", () => {
      panel.querySelectorAll(".iw-tab").forEach(x =>
        x.classList.toggle("active", x === t));
      renderInfoWidget(currentInfoData, t.dataset.tab);
    }));
  // Klick ausserhalb schliesst (aber nicht wenn im Panel geklickt wird)
  document.addEventListener("pointerdown", (e) => {
    if (panel.classList.contains("hidden")) return;
    if (panel.contains(e.target) || btn.contains(e.target)) return;
    panel.classList.add("hidden");
  });
}

let currentInfoData = null;   // Cache: einmal laden pro Oeffnen

async function loadInfoWidget() {
  const body = $("iw-body");
  const cached = localStorage.getItem("astro_info_cache");
  let data = currentInfoData;
  if (!data) {
    try {
      const [stats, hist, cmds] = await Promise.all([
        api("/api/bias-stats"), api("/api/bias-history?days=365"),
        api("/api/telegram-commands")]);
      data = { stats, hist, cmds };
      currentInfoData = data;
      localStorage.setItem("astro_info_cache", JSON.stringify(data));
    } catch (e) {
      console.warn("info-widget offline:", e);
      data = cached ? JSON.parse(cached) : null;
      if (data) data._offline = true;
    }
  }
  renderInfoWidget(data, "bias");
}

function renderInfoWidget(data, tab) {
  const body = $("iw-body");
  if (!data) {
    body.innerHTML = "<div class='iw-empty'>Daten noch nicht verf\u00fcgbar "
      + "(offline und kein gespeicherter Stand).</div>";
    return;
  }
  if (tab === "bot") { body.innerHTML = iwBotHtml(data.cmds); return; }
  body.innerHTML = iwBiasHtml(data.stats, data.hist, !!data._offline);
}

function iwBiasHtml(stats, hist, offline) {
  if (!stats || !stats.buckets) return "<div class='iw-empty'>Noch keine "
    + "Bias-Daten berechnet.</div>";
  const cards = Object.entries(BIAS_LABELS).map(([k, [name, unit]]) => {
    const b = stats.buckets[k] || {};
    const bias = b.bias == null ? "\u2013" : `${b.bias > 0 ? "+" : ""}${b.bias.toFixed(2)} ${unit}`;
    const n = b.sample_n != null ? `n=${b.sample_n}` : "";
    const b7 = b.bias_7d != null
      ? `${b.bias_7d > 0 ? "+" : ""}${b.bias_7d.toFixed(k.startsWith("clouds") ? 1 : 2)} ${unit} <small>(7&nbsp;Tage)</small>`
      : "<small>7-Tage-Wert: zu wenig Daten</small>";
    return `<div class="iw-card ${b.applied ? "" : "iw-inactive"}">
      <div class="iw-card-name">${name}</div>
      <div class="iw-card-val">${bias} <small>insgesamt</small></div>
      <div class="iw-card-val iw-card-7d">${b7}</div>
      <div class="iw-card-n">${n}${b.applied === false ? " \u00b7 zu wenig Daten" : ""}</div>
      <div class="iw-card-tendenz">${biasTendenz(k, b.bias_7d != null ? b.bias_7d : b.bias)}</div>
    </div>`;
  }).join("");
  return `
    <div class="iw-sub">Wie genau stimmen die Prognosen?
      ${offline ? "<span class='iw-offline'>(offline: letzter Stand)</span>"
                : ""} \u00b7 Stand: ${esc(stats.computed_at || "?")}</div>
    <div class="iw-cards">${cards}</div>
    <div class="iw-chart-title">Gesamter Zeitverlauf \u2014 Abweichung = Prognose vs. Realit\u00e4t</div>
    <div class="iw-chart-hint">Kr\u00e4ftige Linie: letzte 7 Tage (Treffsicherheit jetzt) \u00b7 d\u00fcnn gestrichelt: Gesamtdurchschnitt (Referenz)</div>
    ${iwChartSvg(hist || [])}
    <div class="iw-note">Das System sagt tendenziell ${Math.abs((stats.buckets.clouds_le24h || {}).bias || 0) > 3 ? "zu optimistische Wolkenprognosen" : "gute Wolkenprognosen"}. Seeing ist sehr akkurat. Die Korrektur wird t\u00e4glich berechnet und in der Anzeige angewendet.</div>
    <div class="iw-foot">Korrektur auf Anzeige angewendet, nicht auf Rating. Rating bleibt bewusst unkorrigiert, bis sich die Korrektur bew\u00e4hrt hat.</div>`;
}

function iwChartSvg(hist) {
  // Kleines Mehrfach-Liniendiagramm als Inline-SVG (keine externe Lib).
  const W = 320, H = 120, PAD = 6;
  const buckets = Object.keys(BIAS_COLORS);
  const points = {};
  buckets.forEach(b => points[b] = []);
  const dates = [...new Set(hist.map(h => h.computed_at.slice(0, 10)))].sort();
  hist.forEach(h => (points[h.bucket] || []).push(h));
  const vals = hist.filter(h => buckets.includes(h.bucket)).map(h => h.bias);
  if (!vals.length || dates.length < 2)
    return "<div class='iw-empty'>Zeitreihe entsteht \u2013 ab dem zweiten "
      + "Tag sehen Sie hier die Entwicklung.</div>";
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 1) { hi += 0.5; lo -= 0.5; }
  const x = d => PAD + (W - 2 * PAD) * (dates.indexOf(d.slice(0, 10)) / (dates.length - 1));
  const y = v => PAD + (H - 2 * PAD) * (1 - (v - lo) / (hi - lo));
  const zero = lo < 0 && hi > 0
    ? `<line x1="${PAD}" x2="${W - PAD}" y1="${y(0)}" y2="${y(0)}" stroke="#666" stroke-dasharray="2 3" stroke-width="0.7"/>` : "";
  // Y-Skala aus BEIDEN Wertreihen (7T + gesamt)
  const allVals = hist.flatMap(h => buckets.includes(h.bucket)
    ? [h.bias, h.bias_7d].filter(v => v != null) : []);
  let lo2 = Math.min(...allVals), hi2 = Math.max(...allVals);
  if (hi2 - lo2 < 1) { hi2 += 0.5; lo2 -= 0.5; }
  lo = lo2; hi = hi2;
  const lines = buckets.map(b => {
    const pts = points[b].slice().sort((a, c) => a.computed_at < c.computed_at ? -1 : 1);
    if (!pts.length) return "";
    const mk = (key, width, dash) => pts
      .filter(p => p[key] != null)
      .map((p, i, arr) => `${arr.length - 1 - i ? "L" : "M"}${x(p.computed_at).toFixed(1)},${y(p[key]).toFixed(1)}`)
      .join(" ");
    const dots = pts.filter(p => p.bias_7d != null).map(p =>
      `<circle cx="${x(p.computed_at).toFixed(1)}" cy="${y(p.bias_7d).toFixed(1)}" r="2.6" fill="${BIAS_COLORS[b]}"><title>${b}: 7T ${p.bias_7d} (n=${p.n_7d}) | gesamt ${p.bias} (n=${p.sample_n})</title></circle>`).join("");
    const gesamt = `<path d="${mk("bias", 1.6, "")}" fill="none" stroke="${BIAS_COLORS[b]}" stroke-width="1.0" stroke-dasharray="3 3" opacity=".65"/>`;
    const rolling = `<path d="${mk("bias_7d", 1.6, "")}" fill="none" stroke="${BIAS_COLORS[b]}" stroke-width="2.2"/>`;
    return gesamt + rolling + dots;
  }).join("");
  const last = {};
  buckets.forEach(b => {
    const s = points[b].slice().sort((a, c) =>
      a.computed_at < c.computed_at ? -1 : 1);
    if (s.length) last[b] = s[s.length - 1];
  });
  const fmtV = (b, v) => v == null ? "\u2013"
    : `${v > 0 ? "+" : ""}${v.toFixed(b.startsWith("clouds") ? 1 : 2)}`;
  const legend = buckets.map(b => {
    if (!last[b]) return `<span class="iw-legend-empty">
       <i style="background:${BIAS_COLORS[b]}"></i>${BIAS_LABELS[b][0]} (keine Daten)</span>`;
    const v7 = last[b].bias_7d != null ? fmtV(b, last[b].bias_7d) : "\u2013";
    return `<span><i style="background:${BIAS_COLORS[b]}"></i>${BIAS_LABELS[b][0]}
       <b>${v7}</b> / gesamt ${fmtV(b, last[b].bias)}</span>`;
  }).join("");
  const span = `<div class="iw-chart-span">${dates[0].slice(5)} \u2013 ${dates[dates.length - 1].slice(5)} (${dates.length} Tage)</div>`;
  return `<div class="iw-chart"><svg viewBox="0 0 ${W} ${H}" role="img">${zero}${lines}</svg>
    <div class="iw-legend">${legend}</div>${span}</div>`;
}

function iwBotHtml(cmds) {
  const list = (cmds && cmds.commands) || [];
  return `
    <div class="iw-sub">Was der Bot kann</div>
    <table class="iw-cmds">${list.map(c => `
      <tr><td class="iw-cmd">${esc(c.command)}${c.usage ? "<br><small>" + esc(c.usage) + "</small>" : ""}</td>
          <td>${esc(c.description)}</td></tr>`).join("")}
    </table>
    <div class="iw-note">Alle Befehle funktionieren direkt im Chat mit
      ${esc((cmds && cmds.bot_name) || "@AstroCrawler007bot")}.
      Schreibe einfach /help, dann bekommst du diese Liste.</div>`;
}
